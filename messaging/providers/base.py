"""The provider interface every integration implements.

The contract is shaped so the two real targets fit without changes:

* **Twilio** -- form-encoded webhooks, ``X-Twilio-Signature`` (HMAC-SHA1 over
  URL + sorted params), Basic-auth REST API, ``whatsapp:+57...`` addressing.
* **Meta Cloud API** -- JSON webhooks, ``X-Hub-Signature-256`` (HMAC-SHA256
  over the raw body), Bearer-token Graph API, a ``phone_number_id`` per line,
  and a one-off GET verification handshake (``hub.challenge``).

Hence the choices below:

* ``parse_webhook``/``verify_signature`` take the raw Django ``request``, not
  a parsed dict -- Twilio needs form params, Meta needs the *raw bytes* for
  its HMAC, and both need headers.
* ``send_*`` take bare E.164 numbers; any addressing scheme (``whatsapp:``)
  is the provider's private business.
* ``handshake`` exists because Meta verifies the endpoint with a GET before
  it ever POSTs; providers that don't do that inherit the no-op.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import InboundEvent


class MessagingProvider(ABC):
    """One messaging backend (WhatsApp via Twilio, Meta Cloud API, fake...)."""

    #: Registry key and URL slug: ``/webhooks/messaging/<name>/``.
    name: str = ""

    @abstractmethod
    def send_text(self, to: str, body: str) -> str:
        """Send a free-form text to ``to`` (E.164). Returns the provider's
        message id, which later status webhooks will reference.

        Only valid inside the 24-hour customer-service window -- callers
        enforce that (see ``services.send_message``); the provider just sends.
        """

    @abstractmethod
    def send_template(self, to: str, template_name: str, params: dict) -> str:
        """Send a pre-approved template message. The only way to reach someone
        outside the 24-hour window. Returns the provider's message id.

        ``params`` carries the body variables keyed by their number
        (``{"1": "Ana"}``) plus one reserved key every provider must
        tolerate: ``_language``, the template's language code.

        How a call arrives. The one production caller is
        ``messaging.services.send_template`` (the "Enviar plantilla" flow):
        ``to`` is the conversation contact's phone, ``template_name`` is the
        plantilla's ``MessageTemplate.name``, and ``params`` is built there as
        ``{str(number): text}`` for every variable with a non-empty value,
        plus ``_language`` = the plantilla's ``language`` field. Variable keys
        are therefore strings (``"1"``, never ``1``).

        How an implementation should treat it. Copy ``params``
        (``dict(params or {})``) before popping the reserved key, so the
        caller's dict is left untouched -- Meta does, and a Meta test pins
        it. Raise on any failure instead of returning a made-up id: the
        caller catches every exception, marks the Message row FAILED and
        zeroes its billed amount, so nothing is charged for a send the
        provider never accepted. Today: Meta puts ``_language`` in the
        payload's language code and turns the remaining keys into body
        parameters; the fake provider logs the whole dict and returns a
        random id; Twilio raises ``NotImplementedError``.
        """

    @abstractmethod
    def parse_webhook(self, request) -> list[InboundEvent]:
        """Normalize one webhook request into events. Called only after
        ``verify_signature`` has passed. Raise ``ValueError`` on a payload
        that cannot be understood -- the endpoint logs it and still answers
        200 so the provider does not enter a retry storm."""

    @abstractmethod
    def verify_signature(self, request) -> bool:
        """Whether the webhook request genuinely came from the provider.

        Checked *before* the body is trusted in any way; a ``False`` is
        answered with 401 and no processing. Each provider brings its own
        scheme (Twilio HMAC-SHA1, Meta HMAC-SHA256 over the raw body)."""

    def handshake(self, request) -> str | None:
        """Answer a GET verification challenge, or ``None`` if the provider
        has no such thing. Meta sends ``hub.mode=subscribe`` with a
        ``hub.challenge`` to echo; Twilio never GETs the webhook."""
        return None
