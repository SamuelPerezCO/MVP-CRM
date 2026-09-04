"""Tests for core.storage.VercelBlobStorage -- the Blob API is mocked; what
is under test is the Storage contract the rest of the app relies on
(deterministic names, exists()-based idempotency, url() resolution).

TestCase rather than SimpleTestCase: a Blob miss now consults StoredFile
before giving up, so that a deployment which connects a Blob store keeps
serving whatever was written before one existed. That is a real query, and
SimpleTestCase forbids database access."""

from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase

from .storage import VercelBlobStorage

BLOB_URL = "https://store.public.blob.vercel-storage.com/whatsapp/m1.webp"


class VercelBlobStorageTests(TestCase):
    def setUp(self):
        self.storage = VercelBlobStorage()

    @patch("core.storage.vercel_blob.list")
    @patch("core.storage.vercel_blob.put")
    def test_save_uploads_and_keeps_the_name(self, mock_put, mock_list):
        mock_list.return_value = {"blobs": []}  # get_available_name's exists()
        mock_put.return_value = {"url": BLOB_URL}

        name = self.storage.save("whatsapp/m1.webp", ContentFile(b"webp"))

        self.assertEqual(name, "whatsapp/m1.webp")
        path, data = mock_put.call_args.args[:2]
        self.assertEqual(path, "whatsapp/m1.webp")
        self.assertEqual(data, b"webp")

    @patch("core.storage.vercel_blob.list")
    @patch("core.storage.vercel_blob.put")
    def test_url_after_save_needs_no_extra_call(self, mock_put, mock_list):
        mock_list.return_value = {"blobs": []}
        mock_put.return_value = {"url": BLOB_URL}
        self.storage.save("whatsapp/m1.webp", ContentFile(b"webp"))
        mock_list.reset_mock()

        self.assertEqual(self.storage.url("whatsapp/m1.webp"), BLOB_URL)
        mock_list.assert_not_called()

    @patch("core.storage.vercel_blob.list")
    def test_exists_and_url_resolve_through_the_list_endpoint(self, mock_list):
        mock_list.return_value = {
            "blobs": [
                # prefix matching also returns longer pathnames -- only the
                # exact one may count.
                {"pathname": "whatsapp/m1.webp-old-copy", "url": "https://x/nope"},
                {"pathname": "whatsapp/m1.webp", "url": BLOB_URL},
            ]
        }

        self.assertTrue(self.storage.exists("whatsapp/m1.webp"))
        self.assertEqual(self.storage.url("whatsapp/m1.webp"), BLOB_URL)
        # exists() filled the cache; url() must not have listed again.
        self.assertEqual(mock_list.call_count, 1)

    @patch("core.storage.vercel_blob.list")
    def test_missing_blob_is_not_found(self, mock_list):
        mock_list.return_value = {"blobs": []}

        self.assertFalse(self.storage.exists("whatsapp/nope.webp"))
        with self.assertRaises(ValueError):
            self.storage.url("whatsapp/nope.webp")

    @patch("core.storage.vercel_blob.delete")
    @patch("core.storage.vercel_blob.list")
    def test_delete_targets_the_resolved_url(self, mock_list, mock_delete):
        mock_list.return_value = {"blobs": [{"pathname": "a.txt", "url": BLOB_URL}]}

        self.storage.delete("a.txt")

        self.assertEqual(mock_delete.call_args.args[0], BLOB_URL)
