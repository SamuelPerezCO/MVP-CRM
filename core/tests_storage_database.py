"""DatabaseStorage: the no-Blob fallback for a read-only filesystem.

These pin the contract the rest of the app already assumes of a storage
backend -- deterministic names, working exists()/delete(), a URL that serves
the bytes without a session -- because the thing it replaces failed silently
in production for hours before anyone could see why.
"""

from unittest import mock

from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings

from core.models import QuickReply, StoredFile
from core.storage import DatabaseStorage


class DatabaseStorageTests(TestCase):
    def setUp(self):
        self.storage = DatabaseStorage()

    def test_save_then_open_round_trips_the_bytes(self):
        self.storage.save("respuestas/foto.png", ContentFile(b"\x89PNG-data"))
        self.assertEqual(self.storage._open("respuestas/foto.png").read(), b"\x89PNG-data")

    def test_name_is_kept_verbatim_so_exists_can_answer_about_it(self):
        # The Meta webhook writes whatsapp/<media-id> and asks exists() on a
        # retry to avoid downloading the same photo twice; a backend that
        # renamed on save would break that into an infinite re-download.
        name = self.storage.save("whatsapp/wamid-123.webp", ContentFile(b"bytes"))
        self.assertEqual(name, "whatsapp/wamid-123.webp")
        self.assertTrue(self.storage.exists("whatsapp/wamid-123.webp"))
        self.assertFalse(self.storage.exists("whatsapp/never-written.webp"))

    def test_size_reports_the_stored_length(self):
        self.storage.save("a.bin", ContentFile(b"1234567890"))
        self.assertEqual(self.storage.size("a.bin"), 10)

    def test_delete_removes_the_row(self):
        self.storage.save("gone.png", ContentFile(b"x"))
        self.storage.delete("gone.png")
        self.assertFalse(self.storage.exists("gone.png"))
        self.assertEqual(StoredFile.objects.count(), 0)

    def test_url_is_unguessable_rather_than_the_storage_key(self):
        # These files are served without authentication, so the public handle
        # must not be the key -- "plantillas/logo.png" is trivially guessable.
        self.storage.save("plantillas/logo.png", ContentFile(b"x"))
        url = self.storage.url("plantillas/logo.png")
        token = StoredFile.objects.get(name="plantillas/logo.png").token
        self.assertIn(token, url)
        self.assertNotIn("plantillas/logo.png", url)
        self.assertEqual(len(token), 32)

    def test_url_of_an_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            self.storage.url("respuestas/never-saved.png")

    def test_overwriting_a_name_keeps_the_token_stable(self):
        # A regenerated token would 404 every page that had already rendered
        # the old URL, including a WhatsApp message already sent.
        self.storage.save("same.png", ContentFile(b"first"))
        before = StoredFile.objects.get(name="same.png").token
        self.storage._save("same.png", ContentFile(b"second"))
        row = StoredFile.objects.get(name="same.png")
        self.assertEqual(row.token, before)
        self.assertEqual(bytes(row.content), b"second")
        self.assertEqual(row.size, len(b"second"))

    def test_leading_slashes_and_backslashes_normalize_to_one_name(self):
        self.storage.save("respuestas/x.png", ContentFile(b"x"))
        self.assertTrue(self.storage.exists("/respuestas/x.png"))
        self.assertTrue(self.storage.exists("respuestas\\x.png"))


class StoredFileViewTests(TestCase):
    def setUp(self):
        self.storage = DatabaseStorage()
        self.storage.save("respuestas/foto.png", ContentFile(b"PNGBYTES"))
        self.url = self.storage.url("respuestas/foto.png")

    def test_serves_the_bytes_with_the_stored_content_type(self):
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"PNGBYTES")
        self.assertEqual(response["Content-Type"], "image/png")

    def test_served_bytes_cannot_run_as_a_page_on_this_origin(self):
        # Uploaded by an agent, served unauthenticated from the app's own
        # origin: an SVG with <script> opened directly would otherwise run
        # with the session of whoever clicked. Sandboxed and nosniffed.
        response = Client().get(self.url)
        self.assertEqual(response["Content-Security-Policy"], "default-src 'none'; sandbox")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn('filename="foto.png"', response["Content-Disposition"])
        self.assertTrue(response["Content-Disposition"].startswith("inline"))

    def test_unknown_token_is_404_not_500(self):
        self.assertEqual(Client().get("/archivos/" + "0" * 32 + "/x.png").status_code, 404)

    @override_settings(TESTING=False)
    def test_reachable_without_a_session(self):
        # WhatsApp fetches this URL from Meta's servers. A login redirect here
        # means the customer never receives the photo, so the login gate has
        # to let /archivos/ through even for a completely anonymous caller.
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"PNGBYTES")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "core.storage.DatabaseStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class QuickReplyThroughDatabaseStorageTests(TestCase):
    def test_a_quick_reply_image_saves_and_serves(self):
        # The end-to-end shape of the bug this backend exists for: on Vercel
        # this same call raised OSError(30) from FileSystemStorage.
        reply = QuickReply(title="Promo", body="Hola")
        reply.image.save("promo.png", ContentFile(b"IMAGE"), save=True)

        reply.refresh_from_db()
        self.assertTrue(reply.image.name)
        response = Client().get(reply.image.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"IMAGE")


class BlobFallsBackToDatabaseTests(TestCase):
    """Connecting a Blob store must not orphan what the database already holds.

    Everything written while no Blob store existed lives in StoredFile. The
    moment BLOB_READ_WRITE_TOKEN appears, VercelBlobStorage takes over for
    *every* name, including those -- and it used to answer "no blob stored at
    ..." for them, which 500s the page rendering the image rather than showing
    it. Production had two such files when this was written.
    """

    def setUp(self):
        from core.storage import VercelBlobStorage

        # Write through the database backend, as a pre-Blob deployment did.
        DatabaseStorage().save("respuestas/legacy.png", ContentFile(b"OLD"))
        self.blob = VercelBlobStorage()
        # Blob itself knows nothing: every lookup misses.
        patcher = mock.patch.object(self.blob, "_resolve_url", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_url_serves_the_database_copy_instead_of_raising(self):
        self.assertIn(
            StoredFile.objects.get(name="respuestas/legacy.png").token,
            self.blob.url("respuestas/legacy.png"),
        )

    def test_exists_sees_it_so_a_new_upload_cannot_shadow_the_name(self):
        self.assertTrue(self.blob.exists("respuestas/legacy.png"))
        self.assertNotEqual(
            self.blob.get_available_name("respuestas/legacy.png"),
            "respuestas/legacy.png",
        )

    def test_open_reads_the_database_bytes(self):
        self.assertEqual(self.blob._open("respuestas/legacy.png").read(), b"OLD")

    def test_delete_removes_it_rather_than_leaving_it_readable(self):
        self.blob.delete("respuestas/legacy.png")
        self.assertFalse(StoredFile.objects.filter(name="respuestas/legacy.png").exists())

    def test_a_name_in_neither_store_still_raises(self):
        with self.assertRaises(ValueError):
            self.blob.url("respuestas/never-existed.png")
