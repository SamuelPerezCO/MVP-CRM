"""Twilio WhatsApp provider -- NOT IMPLEMENTED YET.

Shape of the eventual implementation, so nothing outside this file changes
when credentials arrive:

* **Sending** -- POST to
  ``https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json``
  with HTTP Basic auth (``TWILIO_ACCOUNT_SID`` / ``TWILIO_AUTH_TOKEN``).
  ``From`` is ``TWILIO_WHATSAPP_FROM`` and both addresses use Twilio's
  ``whatsapp:+57...`` scheme -- prefix on the way out, strip on the way in,
  never let it leak past this module. The response's ``sid`` (``SM...``) is
  the provider message id. Templates are "content templates": send a
  ``ContentSid`` plus ``ContentVariables`` JSON instead of ``Body``.

* **parse_webhook** -- Twilio POSTs *form-encoded* params (``request.POST``),
  one event per request. Inbound messages carry ``MessageSid``, ``From``,
  ``Body``, ``NumMedia``/``MediaUrl0``; status callbacks carry
  ``MessageSid`` + ``MessageStatus`` (map ``queued/sent/delivered/read/
  failed/undelivered`` onto :class:`~messaging.providers.types.MessageStatus`,
  folding ``undelivered`` into ``failed``).

* **verify_signature** -- ``X-Twilio-Signature``: HMAC-SHA1, key =
  ``TWILIO_AUTH_TOKEN``, message = full request URL + form params sorted by
  key and concatenated. The ``twilio`` pip package's ``RequestValidator``
  does exactly this; beware reverse proxies rewriting scheme/host, since the
  URL is part of the signed content.

* **handshake** -- none; Twilio never GETs the webhook (inherits the no-op).
"""

from __future__ import annotations

from .base import MessagingProvider
from .types import InboundEvent


class TwilioProvider(MessagingProvider):
    name = "twilio"

    def send_text(self, to: str, body: str) -> str:
        raise NotImplementedError("Twilio provider pending credentials -- see module docstring")

    def send_template(self, to: str, template_name: str, params: dict) -> str:
        raise NotImplementedError("Twilio provider pending credentials -- see module docstring")

    def parse_webhook(self, request) -> list[InboundEvent]:
        raise NotImplementedError("Twilio provider pending credentials -- see module docstring")

    def verify_signature(self, request) -> bool:
        raise NotImplementedError("Twilio provider pending credentials -- see module docstring")
