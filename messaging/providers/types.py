"""Provider-agnostic value types.

Every provider's webhook payload -- Twilio's form-encoded POST, Meta's nested
JSON, the fake provider's flat JSON -- is normalized into these before the rest
of the app sees it. Nothing outside ``providers/`` should ever touch a raw
provider payload.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


#: Body shown when a media message carries no text of its own -- an image
#: with no caption should still read as something, not as an empty bubble.
#: Shared here (not per-provider) because the Inbox also needs to know these
#: are placeholders: once the image itself renders in the thread, repeating
#: "[sticker]" under it as text is noise (see ``Message.display_body``).
MEDIA_PLACEHOLDERS = {
    "image": "[imagen]",
    "video": "[video]",
    "audio": "[audio]",
    "document": "[documento]",
    "sticker": "[sticker]",
}


class MessageStatus(str, enum.Enum):
    """Delivery lifecycle of an outbound message.

    Values are stored verbatim in ``Message.status``. The order here is the
    canonical progression; :func:`status_rank` turns it into a comparison so
    out-of-order webhook deliveries (Twilio retries, Meta batching) can never
    move a message *backwards* -- e.g. a late "delivered" after "read".
    """

    QUEUED = "queued"        # created locally, not yet accepted by the provider
    SENT = "sent"            # accepted by the provider
    DELIVERED = "delivered"  # reached the recipient's device
    READ = "read"            # recipient opened it
    FAILED = "failed"        # provider gave up (terminal)


_STATUS_ORDER = [
    MessageStatus.QUEUED,
    MessageStatus.SENT,
    MessageStatus.DELIVERED,
    MessageStatus.READ,
]


def status_rank(status: str) -> int:
    """Position of ``status`` in the delivery progression.

    ``failed`` ranks highest: it is terminal, and a retry storm re-delivering
    an old "sent" must not resurrect a message the provider already gave up on.
    Unknown values rank lowest so they can never overwrite anything.
    """
    try:
        return _STATUS_ORDER.index(MessageStatus(status))
    except ValueError:
        return -1 if status != MessageStatus.FAILED.value else len(_STATUS_ORDER)


@dataclass(frozen=True)
class InboundEvent:
    """One normalized event out of a webhook payload.

    A single webhook request can carry many of these (Meta batches, the fake
    provider's status ticks), which is why ``parse_webhook`` returns a list.

    Two shapes share the class, discriminated by ``event_type``:

    * ``"message"`` -- someone wrote to us. ``body``/``media_url`` matter,
      ``status`` is ignored.
    * ``"status"``  -- a delivery receipt for a message *we* sent.
      ``status`` matters, ``body`` is ignored.
    """

    event_type: str
    """``"message"`` or ``"status"``."""

    provider_message_id: str
    """The provider's id for the message. This is the idempotency key: the
    same id arriving twice (providers retry aggressively) must not create a
    second row."""

    from_number: str = ""
    """Sender in E.164 (``+57316...``). Providers that prefix an address
    scheme (Twilio's ``whatsapp:+57...``) strip it in ``parse_webhook``."""

    to_number: str = ""
    """Our number, E.164. Unused while the app has a single inbox; kept so
    multi-number routing needs no interface change."""

    body: str = ""

    media_url: str = ""

    media_type: str = ""
    """What kind of attachment ``media_url`` points at, using the provider's
    coarse vocabulary (a ``MEDIA_PLACEHOLDERS`` key: ``image``, ``video``,
    ``audio``, ``document``, ``sticker``). Empty for text-only messages. The
    Inbox uses it to render images/stickers inline instead of as a download
    link."""

    timestamp: datetime | None = None
    """Provider-reported time, timezone-aware. ``None`` -> receipt time."""

    status: MessageStatus | None = None
    """For ``status`` events: the new delivery state."""

    channel: str = "whatsapp"
    """Conversation channel key (see ``Conversation.CHANNEL_CHOICES``). Meta's
    webhook also carries Messenger/Instagram traffic, so the event says which."""

    contact_name: str = field(default="")
    """Display name if the provider shares it (Meta's ``profile.name``). Used
    only when the phone number is new to the CRM."""

    pricing: dict | None = None
    """For ``status`` events from a provider that reports billing: what the
    platform says this message cost it.

    Meta puts a ``pricing`` object on the ``sent`` status and on one of
    ``delivered``/``read``. It carries no money -- only which *rate bucket*
    applies -- so the CRM keeps its own amount and uses this to correct it
    (``services._apply_status_event``). Normalized keys, all optional because
    Meta's payloads are not consistent about them:

    * ``billable``  -- bool. Being deprecated in favour of ``type``.
    * ``model``     -- ``"PMP"`` (per-message, the default since 2025-07-01)
      or ``"CBP"`` (the legacy conversation model).
    * ``category``  -- the rate Meta actually applied: ``marketing``,
      ``utility``, ``authentication``, ``authentication-international``,
      ``service``, ``marketing_lite``, ``referral_conversion``. This is
      Meta's verdict, which can differ from the category stored on the
      plantilla -- Meta re-categorises templates.
    * ``type``      -- ``regular`` (billable), ``free_customer_service`` or
      ``free_entry_point``.

    ``None`` when the provider says nothing about billing, which is every
    provider but Meta today.
    """
