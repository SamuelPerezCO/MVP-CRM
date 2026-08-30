"""Meta (WhatsApp Cloud API) provider -- NOT IMPLEMENTED YET.

Shape of the eventual implementation, so nothing outside this file changes
when the developer account is unblocked:

* **Sending** -- POST JSON to
  ``https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages`` with
  ``Authorization: Bearer {META_ACCESS_TOKEN}``. Free-form:
  ``{"messaging_product": "whatsapp", "to": "+57...", "type": "text",
  "text": {"body": ...}}``; templates use ``"type": "template"`` with the
  template name, language code and a components array built from ``params``.
  The response's ``messages[0].id`` (``wamid...``) is the provider message id.

* **parse_webhook** -- JSON, heavily nested and batched:
  ``entry[].changes[].value`` holds ``messages[]`` (inbound: ``id``,
  ``from`` -- digits without ``+``, normalize to E.164 -- ``timestamp`` as
  unix seconds, ``text.body`` / media by id) and ``statuses[]`` (``id``,
  ``status`` in sent/delivered/read/failed, ``recipient_id``). One request
  can yield many :class:`~messaging.providers.types.InboundEvent`; also
  ``contacts[].profile.name`` -> ``contact_name``. Media arrives as an id
  that must be resolved to a URL via a separate authorized Graph call.

* **verify_signature** -- ``X-Hub-Signature-256``: ``"sha256=" +
  HMAC_SHA256(META_APP_SECRET, raw request body)``, compared in constant
  time. Must use ``request.body`` bytes exactly as received.

* **handshake** -- Meta GETs the webhook once at subscribe time with
  ``hub.mode=subscribe``, ``hub.verify_token``, ``hub.challenge``: check the
  token equals ``META_VERIFY_TOKEN`` and return ``hub.challenge``; otherwise
  return ``None`` (the endpoint answers 403-ish by not echoing).
"""

from __future__ import annotations

from .base import MessagingProvider
from .types import InboundEvent


class MetaProvider(MessagingProvider):
    name = "meta"

    def send_text(self, to: str, body: str) -> str:
        raise NotImplementedError("Meta provider pending credentials -- see module docstring")

    def send_template(self, to: str, template_name: str, params: dict) -> str:
        raise NotImplementedError("Meta provider pending credentials -- see module docstring")

    def parse_webhook(self, request) -> list[InboundEvent]:
        raise NotImplementedError("Meta provider pending credentials -- see module docstring")

    def verify_signature(self, request) -> bool:
        raise NotImplementedError("Meta provider pending credentials -- see module docstring")

    def handshake(self, request) -> str | None:
        raise NotImplementedError("Meta provider pending credentials -- see module docstring")
