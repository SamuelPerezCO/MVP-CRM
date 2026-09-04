"""Baileys WhatsApp provider -- an unofficial connection, not the Cloud API.

Bridges to a small Node sidecar (``whatsapp-sidecar/``) that holds an actual
WhatsApp Web/Desktop-style session via Baileys (QR-code pairing, no Meta
Developers app, no business verification, no template restriction). This
provider is HTTP-thin by design: all WhatsApp protocol handling lives in the
sidecar, matching how the other providers wrap a vendor's REST API.

* **Sending** -- POST ``{BAILEYS_SIDECAR_URL}/send`` with
  ``{"to": "+57...", "body": "..."}`` and header
  ``X-Sidecar-Secret: {BAILEYS_SIDECAR_SECRET}``. The sidecar's own message
  id (Baileys' ``key.id``) comes back as ``{"id": "..."}`` and is the
  provider message id, same as every other provider.

  There is no separate template mechanism: WhatsApp's "outside the 24h
  window needs a pre-approved template" rule is a Cloud API/BSP policy, not
  something an unofficial client enforces. ``send_template`` renders
  ``params`` into the template string and sends it as plain text -- the
  24-hour gate itself is still enforced one layer up, in
  ``messaging.services.send_message``, same as for every provider.

* **parse_webhook** -- the sidecar POSTs the *exact* payload shape the fake
  provider uses (``{"events": [{...InboundEvent fields...}]}``), because
  there's no vendor format to translate here -- the sidecar was written to
  speak this app's native shape directly.

* **verify_signature** -- a shared secret in ``X-Sidecar-Secret``, compared
  in constant time. Deliberately the same scheme the sidecar itself uses to
  authenticate Django's calls to ``/send`` -- one secret, checked both ways.

* **handshake** -- none; the sidecar never GETs the webhook (inherits the
  no-op default).
"""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from datetime import datetime

import requests
from django.conf import settings
from django.utils import timezone

from .base import MessagingProvider
from .types import InboundEvent, MessageStatus

logger = logging.getLogger(__name__)

#: Seconds to wait on the sidecar before giving up. The sidecar is on the
#: same host/LAN in every setup this provider is meant for -- a demo -- so a
#: slow response means something is actually wrong, not network latency.
_REQUEST_TIMEOUT = 10


class BaileysProvider(MessagingProvider):
    name = "baileys"

    # --- Sending -------------------------------------------------------

    def send_text(self, to: str, body: str) -> str:
        return self._send(to, body)

    def send_template(self, to: str, template_name: str, params: dict) -> str:
        # No real template mechanism outside the Cloud API/BSPs, so this
        # sends plain text. The caller passes the already-rendered body as
        # params["_rendered"] (see MessagingProvider.send_template); without
        # it, fall back to substituting into the name, which is all an older
        # caller supplied.
        params = dict(params or {})
        rendered = params.pop("_rendered", "")
        params.pop("_language", None)
        if not rendered:
            rendered = template_name
            for key, value in params.items():
                rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        return self._send(to, rendered)

    def send_image(self, to: str, image_url: str, caption: str = "") -> str:
        # The sidecar's /send takes an optional image_url; a build that
        # predates it ignores the key and delivers the caption as text.
        return self._send(to, caption, image_url=image_url)

    def _send(self, to: str, body: str, image_url: str = "") -> str:
        url = f"{settings.BAILEYS_SIDECAR_URL.rstrip('/')}/send"
        payload = {"to": to, "body": body}
        if image_url:
            payload["image_url"] = image_url
        response = requests.post(
            url,
            json=payload,
            headers={"X-Sidecar-Secret": settings.BAILEYS_SIDECAR_SECRET},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("id") or f"baileys-{uuid.uuid4().hex}"

    # --- Webhook ---------------------------------------------------------

    def verify_signature(self, request) -> bool:
        secret = settings.BAILEYS_SIDECAR_SECRET
        if not secret:
            # Fail closed, like the Meta provider. baileys is a *real*
            # provider, so registry.webhook_enabled lets its slug answer on
            # every deployment -- an empty or shipped-default secret is
            # therefore an open write endpoint into the database, not a
            # convenience.
            logger.error(
                "BAILEYS_SIDECAR_SECRET is not set -- rejecting the sidecar "
                "webhook. Without it anyone could post messages into the CRM."
            )
            return False
        supplied = request.headers.get("X-Sidecar-Secret", "")
        return hmac.compare_digest(supplied, secret)

    def parse_webhook(self, request) -> list[InboundEvent]:
        """Payload shape: ``{"events": [{...InboundEvent fields...}]}`` --
        identical to the fake provider's, since the sidecar speaks it
        natively (see module docstring)."""
        try:
            payload = json.loads(request.body)
            raw_events = payload["events"]
        except (ValueError, KeyError) as exc:
            raise ValueError(f"unparseable baileys webhook payload: {exc}") from exc

        events = []
        for raw in raw_events:
            timestamp = None
            if raw.get("timestamp"):
                timestamp = datetime.fromisoformat(raw["timestamp"])
                if timezone.is_naive(timestamp):
                    timestamp = timezone.make_aware(timestamp)
            events.append(
                InboundEvent(
                    event_type=raw.get("event_type", "message"),
                    provider_message_id=raw.get("provider_message_id")
                    or f"baileys-in-{uuid.uuid4().hex}",
                    from_number=raw.get("from_number", ""),
                    to_number=raw.get("to_number", ""),
                    body=raw.get("body", ""),
                    media_url=raw.get("media_url", ""),
                    media_type=raw.get("media_type", ""),
                    timestamp=timestamp,
                    status=MessageStatus(raw["status"]) if raw.get("status") else None,
                    channel=raw.get("channel", "whatsapp"),
                    contact_name=raw.get("contact_name", ""),
                )
            )
        return events
