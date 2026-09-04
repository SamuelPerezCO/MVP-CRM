"""Django file storage on Vercel Blob.

Vercel's serverless functions have no persistent filesystem, so anything
saved to ``MEDIA_ROOT`` in production disappears after the request -- which
silently broke every ``default_storage.save()`` (WhatsApp media downloads,
template header uploads). This backend puts those bytes in the project's
Vercel Blob store instead and hands back the store's public CDN URLs.

Activated by settings only when ``BLOB_READ_WRITE_TOKEN`` is present (Vercel
adds it to the environment when the Blob store is connected to the project);
local development and tests keep the filesystem storage untouched.

Names map to blob pathnames verbatim -- no random suffix -- so a caller that
wrote ``whatsapp/<media-id>.webp`` can ``exists()`` the same name later and
find it (the Meta webhook relies on this for retry idempotency). ``url()``
answers from a per-process cache filled by ``_save`` and, on a miss (other
lambda instance, later request), by one call to the Blob list endpoint.
"""

from __future__ import annotations

import mimetypes
import posixpath
import uuid

import requests
import vercel_blob
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.urls import reverse
from django.utils.deconstruct import deconstructible

_REQUEST_TIMEOUT = 10


@deconstructible
class VercelBlobStorage(Storage):
    def __init__(self):
        # pathname -> public URL, per process. Blob URLs are stable for the
        # life of the file, so positive entries never go stale.
        self._url_cache: dict[str, str] = {}

    @staticmethod
    def _normalize(name: str) -> str:
        return name.replace("\\", "/").lstrip("/")

    def _save(self, name: str, content) -> str:
        name = self._normalize(name)
        data = content.read()
        # Overwrite allowed: Storage.save() only reaches here after
        # get_available_name() picked a free name, so an actual overwrite
        # only happens when two writers race for the same deterministic
        # pathname -- and then both wrote the same downloaded bytes.
        result = vercel_blob.put(
            name, data, {"allowOverwrite": "true"}, timeout=_REQUEST_TIMEOUT
        )
        self._url_cache[name] = result["url"]
        return name

    def _open(self, name: str, mode: str = "rb") -> ContentFile:
        response = requests.get(self.url(name), timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        return ContentFile(response.content, name=name)

    def exists(self, name: str) -> bool:
        return self._resolve_url(name) is not None

    def url(self, name: str) -> str:
        url = self._resolve_url(name)
        if url is None:
            raise ValueError(f"no blob stored at {name!r}")
        return url

    def delete(self, name: str) -> None:
        url = self._resolve_url(name)
        if url is not None:
            vercel_blob.delete(url, timeout=_REQUEST_TIMEOUT)
            self._url_cache.pop(self._normalize(name), None)

    def _resolve_url(self, name: str) -> str | None:
        name = self._normalize(name)
        cached = self._url_cache.get(name)
        if cached is not None:
            return cached
        # prefix matching, so "a.webp" also returns "a.webp-longer-name";
        # only the exact pathname counts.
        listing = vercel_blob.list(
            {"prefix": name, "limit": "10"}, timeout=_REQUEST_TIMEOUT
        )
        for blob in listing.get("blobs", []):
            if blob.get("pathname") == name:
                self._url_cache[name] = blob["url"]
                return blob["url"]
        return None


@deconstructible
class DatabaseStorage(Storage):
    """Django file storage in a database table -- the no-Blob fallback.

    Same job as :class:`VercelBlobStorage` and the same contract, for a
    deployment where the filesystem is read-only and no Blob store is
    connected: the bytes go in ``core.models.StoredFile`` and ``url()`` hands
    back a route this app serves itself (``core.views.stored_file``).

    Chosen by settings only when the app is running on Vercel *without*
    ``BLOB_READ_WRITE_TOKEN``. Blob is still preferred wherever it is
    available -- it serves from a CDN and keeps large binaries out of the
    row store -- so connecting a Blob store later silently takes over, with
    no code change and nothing to migrate for files written from then on.

    Names are stored verbatim, exactly as the Blob backend keeps them, so a
    caller that wrote ``whatsapp/<media-id>.webp`` can ``exists()`` the same
    name later and find it (the Meta webhook relies on this for retry
    idempotency).
    """

    @staticmethod
    def _normalize(name: str) -> str:
        return name.replace("\\", "/").lstrip("/")

    def _save(self, name: str, content) -> str:
        from core.models import StoredFile

        name = self._normalize(name)
        data = content.read()
        content_type = getattr(content, "content_type", "") or (
            mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
        # Overwrite allowed for the same reason as the Blob backend: save()
        # only reaches here after get_available_name() picked a free name, so
        # a real collision means two writers raced for the same deterministic
        # pathname -- and then both wrote the same downloaded bytes. The token
        # is only generated for a genuinely new row, so a file's public URL
        # never changes underneath a page that already rendered it.
        row = StoredFile.objects.filter(name=name).first()
        if row is None:
            StoredFile.objects.create(
                name=name,
                token=uuid.uuid4().hex,
                content=data,
                content_type=content_type,
                size=len(data),
            )
        else:
            row.content = data
            row.content_type = content_type
            row.size = len(data)
            row.save(update_fields=["content", "content_type", "size"])
        return name

    def _open(self, name: str, mode: str = "rb") -> ContentFile:
        from core.models import StoredFile

        row = StoredFile.objects.filter(name=self._normalize(name)).first()
        if row is None:
            raise FileNotFoundError(name)
        return ContentFile(bytes(row.content), name=name)

    def exists(self, name: str) -> bool:
        from core.models import StoredFile

        return StoredFile.objects.filter(name=self._normalize(name)).exists()

    def size(self, name: str) -> int:
        from core.models import StoredFile

        row = StoredFile.objects.filter(name=self._normalize(name)).first()
        if row is None:
            raise FileNotFoundError(name)
        return row.size

    def url(self, name: str) -> str:
        from core.models import StoredFile

        normalized = self._normalize(name)
        token = (
            StoredFile.objects.filter(name=normalized)
            .values_list("token", flat=True)
            .first()
        )
        if token is None:
            raise ValueError(f"no file stored at {name!r}")
        # The basename rides along so a saved or shared link keeps a sensible
        # filename; only the token identifies the row.
        return reverse(
            "stored_file",
            kwargs={"token": token, "filename": posixpath.basename(normalized)},
        )

    def delete(self, name: str) -> None:
        from core.models import StoredFile

        StoredFile.objects.filter(name=self._normalize(name)).delete()
