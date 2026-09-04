"""Meta (WhatsApp Cloud API) provider -- the official WhatsApp integration.

Talks to the Graph API with a Bearer token and a ``phone_number_id``; Meta
pushes inbound messages and delivery receipts to
``/webhooks/messaging/meta/`` as nested, batched JSON signed with the app
secret.

* **Sending** -- POST JSON to
  ``{GRAPH}/{version}/{META_PHONE_NUMBER_ID}/messages`` with
  ``Authorization: Bearer {META_ACCESS_TOKEN}``. Free-form text is
  ``{"messaging_product": "whatsapp", "to": ..., "type": "text", ...}``;
  templates use ``"type": "template"`` with a name, a language code and a
  components array built from ``params``. The response's
  ``messages[0].id`` (``wamid...``) is the provider message id that later
  status webhooks reference.

* **parse_webhook** -- one request carries ``entry[].changes[].value``,
  which holds ``messages[]`` (inbound) and ``statuses[]`` (receipts for our
  own sends), so a single POST can yield many
  :class:`~messaging.providers.types.InboundEvent`. Numbers arrive as bare
  digits and are normalized back to E.164; timestamps are unix seconds.

* **verify_signature** -- ``X-Hub-Signature-256``:
  ``"sha256=" + HMAC_SHA256(META_APP_SECRET, raw body)``, compared in
  constant time against ``request.body`` exactly as received. An unset
  ``META_APP_SECRET`` rejects everything rather than waving traffic through:
  a webhook that anyone on the internet can POST to is the whole attack
  surface of this integration.

* **handshake** -- Meta GETs the webhook once at subscribe time with
  ``hub.mode=subscribe``, ``hub.verify_token`` and ``hub.challenge``. The
  token must equal ``META_VERIFY_TOKEN`` (again, an unset setting fails
  closed) before the challenge is echoed back.

Media caveat: an inbound photo/audio/document arrives as an *id*, not a URL.
Resolving it costs a second authorized Graph call, and what comes back is a
short-lived, token-gated CDN link -- unusable from a browser (the download
needs the Bearer token) and dead within minutes. So the webhook downloads the
bytes right then, while the link is fresh, into Django's default storage
(``MEDIA_ROOT`` locally; swap ``STORAGES`` for object storage without touching
this module) and stores *our* durable URL on the message. Both Graph calls and
the download run inline on the webhook request under tight timeouts and a size
cap, and any failure is logged rather than raised -- a webhook never fails
over an image, it just lands the message without media.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import re
from datetime import datetime, timezone as dt_timezone

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.crypto import constant_time_compare

from .base import MessagingProvider
from .types import MEDIA_PLACEHOLDERS, InboundEvent, MessageStatus

logger = logging.getLogger(__name__)

#: Pinned Graph API version. Meta keeps each version working for ~2 years and
#: the dashboard's sample calls show the current one; bump deliberately after
#: reading the changelog, never implicitly.
_GRAPH_API_VERSION = "v25.0"
_GRAPH_BASE = "https://graph.facebook.com"

_REQUEST_TIMEOUT = 10
#: Media resolution + download happen inline on the webhook request, which
#: Meta expects answered fast -- a tighter budget than a send the user is
#: waiting on. Applied per call (id lookup, then download).
_MEDIA_TIMEOUT = 5

#: Refuse to pull anything bigger into memory on a webhook thread. Covers
#: every sticker/image and most audio/video WhatsApp accepts; an oversized
#: document simply lands without media (logged), the message itself survives.
_MEDIA_MAX_BYTES = 16 * 1024 * 1024

#: Where downloaded media lives inside default storage.
_MEDIA_STORAGE_DIR = "whatsapp"

#: The mimes WhatsApp actually sends, mapped to extensions explicitly --
#: ``mimetypes.guess_extension`` is platform-dependent for some of these
#: (e.g. ``image/jpeg`` -> ``.jpe`` on some systems). Anything else falls
#: back to ``guess_extension``.
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "audio/aac": ".aac",
    "audio/amr": ".amr",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "application/pdf": ".pdf",
}

#: Language for templates when ``params`` doesn't override it (see
#: :meth:`MetaProvider.send_template`). The CRM writes to Colombian numbers.
_DEFAULT_TEMPLATE_LANGUAGE = "es"

#: Meta's status vocabulary happens to match ours one-for-one; the map exists
#: so an unknown future value is skipped instead of blowing up the batch.
_STATUS_MAP = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "read": MessageStatus.READ,
    "failed": MessageStatus.FAILED,
}

#: The same webhook carries Messenger and Instagram traffic once those
#: products are added to the app; the top-level ``object`` says which.
_OBJECT_CHANNELS = {
    "whatsapp_business_account": "whatsapp",
    "page": "messenger",
    "instagram": "instagram-dm",
}

#: Message types that carry a media id plus an optional caption. Placeholder
#: bodies for caption-less media live in ``types.MEDIA_PLACEHOLDERS``, shared
#: with the Inbox.
_MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")


def _to_e164(number: str) -> str:
    """Meta reports numbers as bare digits (``573000000099``); the CRM stores
    E.164. Anything already prefixed is left alone."""
    number = (number or "").strip()
    if not number:
        return ""
    return number if number.startswith("+") else f"+{number}"


def _parse_timestamp(raw) -> datetime | None:
    """Unix seconds (as a string, in Meta's payloads) -> aware datetime."""
    try:
        return datetime.fromtimestamp(int(raw), tz=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


class MetaProvider(MessagingProvider):
    name = "meta"

    # --- Sending -----------------------------------------------------------

    def send_text(self, to: str, body: str) -> str:
        return self._post_message(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                # preview_url off: link previews are fetched by Meta and leak
                # that a message was processed before the recipient opens it.
                "text": {"preview_url": False, "body": body},
            }
        )

    def send_template(self, to: str, template_name: str, params: dict) -> str:
        """Send a pre-approved template -- the only way out of the 24h window.

        ``params`` fills the template's body placeholders. Two shapes, picked
        automatically: all-numeric keys (``{"1": "Ana"}``) become positional
        parameters in numeric order; any other key becomes a *named*
        parameter, matching templates authored with ``{{nombre}}``
        placeholders. The reserved key ``_language`` overrides the language
        code for one send; ``_body`` (the caller's own rendering of the
        template, for providers that have no template mechanism) is dropped
        here -- Meta renders the approved copy from its own records, and
        sending our text would be a second, unapproved body.

        Both reserved keys are popped from a *copy* of ``params`` (the
        ``dict(...)`` in the first statement below), so the caller's dict
        comes back exactly as it went in. Whatever survives the two pops is
        handed to :meth:`_build_template_parameters`, which is why ``_body``
        has to go before that call and not after. The finished payload goes
        through :meth:`_post_message`, and the ``wamid`` it returns is the
        value handed back to ``services.send_template``.
        """
        params = dict(params or {})
        language = str(params.pop("_language", _DEFAULT_TEMPLATE_LANGUAGE))
        # ``pop`` with a ``None`` default removes ``_body`` when present and is
        # a no-op when absent, so callers written before this key existed
        # still work. It must happen before ``_build_template_parameters``
        # below: that helper treats every remaining key as a body variable,
        # and a leftover ``_body`` would fail its all-numeric check and ship
        # to Graph as a named parameter the approved template never declared.
        params.pop("_body", None)

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        parameters = self._build_template_parameters(params)
        if parameters:
            payload["template"]["components"] = [
                {"type": "body", "parameters": parameters}
            ]
        return self._post_message(payload)

    @staticmethod
    def _build_template_parameters(params: dict) -> list[dict]:
        if not params:
            return []
        if all(str(key).isdigit() for key in params):
            ordered = sorted(params.items(), key=lambda item: int(item[0]))
            return [{"type": "text", "text": str(value)} for _, value in ordered]
        return [
            {"type": "text", "parameter_name": str(key), "text": str(value)}
            for key, value in params.items()
        ]

    def _post_message(self, payload: dict) -> str:
        if not settings.META_ACCESS_TOKEN or not settings.META_PHONE_NUMBER_ID:
            raise RuntimeError(
                "Meta provider is not configured: set META_ACCESS_TOKEN and "
                "META_PHONE_NUMBER_ID (see .env.example)"
            )

        url = (
            f"{_GRAPH_BASE}/{_GRAPH_API_VERSION}/"
            f"{settings.META_PHONE_NUMBER_ID}/messages"
        )
        response = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.META_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        )

        if response.status_code >= 400:
            # Graph puts the actionable part (invalid template, number not in
            # the test allow-list, expired token) in the body, which
            # raise_for_status() throws away. Log it -- never the token.
            logger.error(
                "meta send failed: HTTP %s to=%s body=%s",
                response.status_code,
                payload.get("to"),
                response.text[:500],
            )
        response.raise_for_status()

        data = response.json()
        try:
            return data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"meta send response without a message id: {data!r}") from exc

    # --- Webhook -----------------------------------------------------------

    def verify_signature(self, request) -> bool:
        secret = settings.META_APP_SECRET
        if not secret:
            logger.error(
                "META_APP_SECRET is not set -- rejecting the Meta webhook. "
                "Without it any caller could post messages into the CRM."
            )
            return False

        header = request.headers.get("X-Hub-Signature-256", "")
        if not header.startswith("sha256="):
            return False

        expected = hmac.new(
            secret.encode("utf-8"), request.body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(header[len("sha256="):], expected)

    def handshake(self, request) -> str | None:
        """Meta's one-off subscribe check: echo ``hub.challenge`` only when
        ``hub.verify_token`` matches ours."""
        token = settings.META_VERIFY_TOKEN
        if not token:
            logger.error("META_VERIFY_TOKEN is not set -- refusing the handshake")
            return None
        if request.GET.get("hub.mode") != "subscribe":
            return None
        if not constant_time_compare(request.GET.get("hub.verify_token", ""), token):
            logger.warning("meta handshake rejected: verify token mismatch")
            return None
        return request.GET.get("hub.challenge")

    def parse_webhook(self, request) -> list[InboundEvent]:
        """Flatten ``entry[].changes[].value`` into a list of events.

        Meta batches: one POST can carry several entries, each with inbound
        messages *and* delivery receipts. Anything unrecognized inside a
        batch is skipped with a log line rather than failing the whole
        request -- the rest of the batch is still real traffic.
        """
        try:
            payload = json.loads(request.body)
            entries = payload["entry"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"unparseable meta webhook payload: {exc}") from exc
        if not isinstance(entries, list):
            raise ValueError("meta webhook 'entry' is not a list")

        channel = _OBJECT_CHANNELS.get(payload.get("object", ""), "whatsapp")
        events: list[InboundEvent] = []

        for entry in entries:
            for change in (entry or {}).get("changes", []):
                value = (change or {}).get("value", {}) or {}
                our_number = _to_e164(
                    (value.get("metadata") or {}).get("display_phone_number", "")
                )
                # contacts[] names the sender; keyed by wa_id so a batch with
                # several senders attributes each name to the right message.
                names = {
                    contact.get("wa_id", ""): (contact.get("profile") or {}).get("name", "")
                    for contact in value.get("contacts", []) or []
                }

                for raw in value.get("messages", []) or []:
                    event = self._parse_message(raw, names, our_number, channel)
                    if event is not None:
                        events.append(event)

                for raw in value.get("statuses", []) or []:
                    event = self._parse_status(raw, channel)
                    if event is not None:
                        events.append(event)

        return events

    def _parse_message(
        self, raw: dict, names: dict, our_number: str, channel: str
    ) -> InboundEvent | None:
        message_id = (raw or {}).get("id")
        if not message_id:
            logger.warning("meta webhook: message without an id, skipped")
            return None

        sender = raw.get("from", "")
        message_type = raw.get("type", "")
        body = ""
        media_url = ""
        media_type = ""

        if message_type == "text":
            body = (raw.get("text") or {}).get("body", "")
        elif message_type in _MEDIA_TYPES:
            media = raw.get(message_type) or {}
            body = media.get("caption", "") or MEDIA_PLACEHOLDERS.get(message_type, "")
            media_url = self._fetch_and_store_media(media.get("id", ""))
            media_type = message_type if media_url else ""
        elif message_type == "button":
            body = (raw.get("button") or {}).get("text", "")
        elif message_type == "interactive":
            interactive = raw.get("interactive") or {}
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            body = reply.get("title", "")
        elif message_type == "location":
            location = raw.get("location") or {}
            body = location.get("name") or (
                f"[ubicación] {location.get('latitude')}, {location.get('longitude')}"
            )
        else:
            # Contacts, orders, system messages, reactions: recorded so the
            # thread doesn't silently lose a turn, but not interpreted.
            logger.info("meta webhook: unhandled message type %r", message_type)
            body = f"[{message_type or 'mensaje no soportado'}]"

        return InboundEvent(
            event_type="message",
            provider_message_id=message_id,
            from_number=_to_e164(sender),
            to_number=our_number,
            body=body,
            media_url=media_url,
            media_type=media_type,
            timestamp=_parse_timestamp(raw.get("timestamp")),
            channel=channel,
            contact_name=names.get(sender, ""),
        )

    def _parse_status(self, raw: dict, channel: str) -> InboundEvent | None:
        message_id = (raw or {}).get("id")
        status = _STATUS_MAP.get(raw.get("status", ""))
        if not message_id or status is None:
            logger.info("meta webhook: unusable status %r, skipped", raw)
            return None

        if status is MessageStatus.FAILED:
            # Why it failed only ever appears here; without this line a failed
            # send is a red tick in the UI with no explanation anywhere.
            logger.warning(
                "meta reported a failed message id=%s errors=%s",
                message_id,
                raw.get("errors"),
            )

        return InboundEvent(
            event_type="status",
            provider_message_id=message_id,
            from_number="",
            to_number=_to_e164(raw.get("recipient_id", "")),
            timestamp=_parse_timestamp(raw.get("timestamp")),
            status=status,
            channel=channel,
        )

    def _fetch_and_store_media(self, media_id: str) -> str:
        """Trade a media id for a durable URL of our own.

        Meta's CDN link is token-gated and expires within minutes -- useless
        to the browser and to history. So: resolve the id, pull the bytes down
        while the link is fresh, save them into default storage and return
        *that* URL. Idempotent per media id: a webhook retry finds the
        already-saved file instead of downloading again.

        Fails soft: a webhook must not 500 -- or hang -- because a download
        went wrong. Returning "" lands the message without media.
        """
        if not media_id or not settings.META_ACCESS_TOKEN:
            return ""
        headers = {"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"}
        try:
            response = requests.get(
                f"{_GRAPH_BASE}/{_GRAPH_API_VERSION}/{media_id}",
                headers=headers,
                timeout=_MEDIA_TIMEOUT,
            )
            response.raise_for_status()
            info = response.json()
            cdn_url = info.get("url", "")
            if not cdn_url:
                return ""

            name = self._storage_name(media_id, info.get("mime_type", ""))
            if default_storage.exists(name):
                return default_storage.url(name)

            if int(info.get("file_size") or 0) > _MEDIA_MAX_BYTES:
                logger.warning(
                    "meta: media %s reports %s bytes, over the cap -- not stored",
                    media_id,
                    info.get("file_size"),
                )
                return ""

            data = self._download(cdn_url, headers, media_id)
            if data is None:
                return ""
            return default_storage.url(default_storage.save(name, ContentFile(data)))
        except (requests.RequestException, ValueError, OSError):
            logger.exception("meta: could not fetch media id %s", media_id)
            return ""

    @staticmethod
    def _download(url: str, headers: dict, media_id: str) -> bytes | None:
        """The CDN download itself, streamed so an oversized (or lying about
        its ``file_size``) file is abandoned at the cap rather than read whole
        into a webhook thread's memory."""
        response = requests.get(
            url, headers=headers, timeout=_MEDIA_TIMEOUT, stream=True
        )
        with response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > _MEDIA_MAX_BYTES:
                    logger.warning(
                        "meta: media %s exceeded the size cap mid-download", media_id
                    )
                    return None
                chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _storage_name(media_id: str, mime_type: str) -> str:
        """Deterministic storage path for a media id, so retries can find the
        file, with an extension the browser can trust for Content-Type."""
        mime = (mime_type or "").split(";")[0].strip().lower()
        extension = _MIME_EXTENSIONS.get(mime) or mimetypes.guess_extension(mime) or ""
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", media_id)
        return f"{_MEDIA_STORAGE_DIR}/{safe_id}{extension}"
