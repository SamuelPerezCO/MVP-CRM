"""Application services: everything between the UI/webhook and the provider.

Two entry points matter:

* :func:`process_inbound_events` -- the "heavy" half of webhook handling,
  kept out of the view so it can move behind a task queue (Celery/RQ) later
  without touching the endpoint: the view would enqueue the normalized events
  instead of calling this directly. There is no queue today (requirements.txt
  is Django only), so it runs synchronously.
* :func:`send_message` -- the only way the app sends a free-form text (or
  a quick reply's image). It is where the 24-hour window rule lives.
* :func:`send_template` -- the one send allowed outside that window: a
  pre-approved plantilla, which is how a conversation is started from our
  side (see :func:`start_conversation`).
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Client

from .models import Conversation, ConversationTag, Message, Tag
from .providers.registry import get_provider
from .providers.types import InboundEvent, MessageStatus, status_rank

logger = logging.getLogger(__name__)


class SendWindowClosed(Exception):
    """Raised when a free-form send is attempted outside the 24-hour window.

    A WhatsApp platform rule: after 24h of customer silence only pre-approved
    templates may be sent. Enforced here so it fails loudly in development
    instead of surfacing as provider errors in production.
    """


class SendFailed(Exception):
    """The provider rejected or errored on a send. The Message row is kept
    with ``status=failed`` so the thread shows what happened."""


# --- Sending ---------------------------------------------------------------


def send_message(
    conversation: Conversation,
    body: str,
    user=None,
    *,
    image_url: str = "",
) -> Message:
    """Send a free-form message in ``conversation`` and record it.

    Text by default; with ``image_url`` (a public URL -- a quick reply's
    stored picture) the provider ships the image and ``body`` rides along as
    its caption. Either way it is a free-form send, bound by the same 24h
    window: media is no more allowed outside it than text is.

    The Message row is created *before* the provider call (status ``queued``)
    so a crash mid-send leaves evidence rather than losing the message; the
    provider's id is attached once the call returns.

    Raises :class:`SendWindowClosed` outside the 24h window and
    :class:`SendFailed` when the provider errors (row kept as ``failed``).
    """
    if not conversation.is_within_24h_window:
        raise SendWindowClosed(
            "La ventana de 24 horas está cerrada: WhatsApp solo permite enviar "
            "una plantilla aprobada hasta que el cliente vuelva a escribir. "
            "(Free-form sends outside the 24h customer-service window are "
            "rejected by the platform -- use send_template.)"
        )

    message = Message.objects.create(
        conversation=conversation,
        direction=Message.OUTBOUND,
        body=body,
        media_url=image_url,
        media_type="image" if image_url else "",
        status=MessageStatus.QUEUED.value,
        sent_by=user if getattr(user, "is_authenticated", False) else None,
    )

    provider = get_provider()
    try:
        if image_url:
            provider_id = provider.send_image(
                to=conversation.contact.phone, image_url=image_url, caption=body
            )
        else:
            provider_id = provider.send_text(to=conversation.contact.phone, body=body)
    except Exception as exc:
        message.status = MessageStatus.FAILED.value
        message.save(update_fields=["status"])
        logger.exception("send_text failed for conversation %s", conversation.pk)
        raise SendFailed(str(exc)) from exc

    message.provider_message_id = provider_id
    message.save(update_fields=["provider_message_id"])

    conversation.last_message_at = message.timestamp
    conversation.save(update_fields=["last_message_at"])
    return message


def send_template(conversation: Conversation, template, user=None) -> Message:
    """Send a WhatsApp plantilla in ``conversation`` and record it.

    The one send that works *outside* the 24h window -- it is how a
    conversation starts (a client who has never written in) or restarts (one
    who went quiet). The rendered body (samples substituted for {{n}}, see
    core.plantillas.render_body) is what the thread shows; the provider gets
    the template's name and its parameters, which is what Meta actually
    delivers. Providers without a template mechanism render the same text.

    No window check on purpose; that is the point of a template. Raises
    :class:`SendFailed` when the provider errors (row kept as ``failed``).
    """
    from core import plantillas  # local: core imports messaging, not the reverse

    body = plantillas.render_body(template)
    message = Message.objects.create(
        conversation=conversation,
        direction=Message.OUTBOUND,
        body=body,
        status=MessageStatus.QUEUED.value,
        sent_by=user if getattr(user, "is_authenticated", False) else None,
    )

    samples = template.body_sample_values or []
    params = {str(index + 1): value for index, value in enumerate(samples) if value}
    params["_language"] = template.language
    # For providers with no template mechanism (Baileys), so they send the
    # message rather than the template's name -- see MessagingProvider.
    params["_rendered"] = body

    provider = get_provider()
    try:
        provider_id = provider.send_template(
            to=conversation.contact.phone, template_name=template.name, params=params
        )
    except Exception as exc:
        message.status = MessageStatus.FAILED.value
        message.save(update_fields=["status"])
        logger.exception("send_template failed for conversation %s", conversation.pk)
        raise SendFailed(str(exc)) from exc

    message.provider_message_id = provider_id
    message.save(update_fields=["provider_message_id"])

    conversation.last_message_at = message.timestamp
    conversation.save(update_fields=["last_message_at"])
    return message


def start_conversation(contact: Client, channel: str = "whatsapp") -> Conversation:
    """The thread to send a first message in: the contact's open one on that
    channel, or a fresh row. Same rule as inbound routing, so an agent
    starting a chat and a customer writing in land in the same place."""
    return _get_or_create_open_conversation(contact, channel)


# --- Inbound processing -----------------------------------------------------


def process_inbound_events(events: list[InboundEvent]) -> None:
    """Apply normalized webhook events to the database.

    Each event is isolated: one bad event is logged and skipped, the rest
    still land -- the webhook has already answered 200 by contract, so there
    is no one left to report a partial failure to.
    """
    for event in events:
        try:
            with transaction.atomic():
                if event.event_type == "status":
                    _apply_status_event(event)
                elif event.event_type == "outbound_message":
                    _apply_outbound_event(event)
                else:
                    _apply_message_event(event)
        except Exception:
            logger.exception("failed to process inbound event %r", event)


def _apply_message_event(event: InboundEvent) -> None:
    """Someone wrote to us: upsert the Client, land the Message."""
    if not event.from_number:
        raise ValueError("message event without from_number")

    # Idempotency first: providers retry deliveries aggressively, and the
    # same provider_message_id must never become two rows. The unique
    # constraint on the column backs this up against races.
    if Message.objects.filter(provider_message_id=event.provider_message_id).exists():
        logger.info("skipping duplicate message %s", event.provider_message_id)
        return

    contact = _upsert_contact(event.from_number, event.contact_name, event.channel)
    conversation = _get_or_create_open_conversation(contact, event.channel)

    timestamp = event.timestamp or timezone.now()
    try:
        Message.objects.create(
            conversation=conversation,
            direction=Message.INBOUND,
            body=event.body,
            media_url=event.media_url,
            media_type=event.media_type,
            # Inbound rows are "delivered" by definition -- they reached us.
            status=MessageStatus.DELIVERED.value,
            provider_message_id=event.provider_message_id,
            timestamp=timestamp,
        )
    except IntegrityError:
        # A concurrent retry won the race between our exists() check and the
        # insert; the message is already stored, which is all that matters.
        logger.info("duplicate message %s lost insert race", event.provider_message_id)
        return

    # An inbound message always (re)opens the thread and the 24h window.
    conversation.last_message_at = timestamp
    conversation.last_inbound_at = timestamp
    conversation.unread_count += 1
    if conversation.status == Conversation.RESOLVED:
        conversation.status = Conversation.OPEN
    conversation.save(
        update_fields=["last_message_at", "last_inbound_at", "unread_count", "status"]
    )


def _apply_outbound_event(event: InboundEvent) -> None:
    """A message we sent through a channel other than this app's own Inbox --
    e.g. an agent replying straight from the paired WhatsApp phone instead of
    the CRM. Recorded so the thread reflects what was actually said either
    way, but unlike :func:`_apply_message_event` it does not touch
    ``last_inbound_at``/``unread_count``/``status``: those track the customer
    side of the 24h window and unread state, neither of which a message we
    sent affects (matching :func:`send_message`, the Inbox's own send path)."""
    if not event.to_number:
        raise ValueError("outbound event without to_number")

    if Message.objects.filter(provider_message_id=event.provider_message_id).exists():
        logger.info("skipping duplicate message %s", event.provider_message_id)
        return

    # No contact_name here: the provider's display name on one of *our* sends
    # is our own name, not the customer's -- useless for naming their Client.
    contact = _upsert_contact(event.to_number, "", event.channel)
    conversation = _get_or_create_open_conversation(contact, event.channel)

    timestamp = event.timestamp or timezone.now()
    try:
        Message.objects.create(
            conversation=conversation,
            direction=Message.OUTBOUND,
            body=event.body,
            media_url=event.media_url,
            media_type=event.media_type,
            status=MessageStatus.DELIVERED.value,
            provider_message_id=event.provider_message_id,
            timestamp=timestamp,
        )
    except IntegrityError:
        logger.info("duplicate message %s lost insert race", event.provider_message_id)
        return

    conversation.last_message_at = timestamp
    conversation.save(update_fields=["last_message_at"])


def _apply_status_event(event: InboundEvent) -> None:
    """A delivery receipt for a message we sent: move its status forward."""
    if event.status is None:
        raise ValueError("status event without a status")

    message = Message.objects.filter(
        provider_message_id=event.provider_message_id
    ).first()
    if message is None:
        # Receipts can outrun the send's DB commit, or reference messages
        # sent outside this app. Nothing to update either way.
        logger.info("status for unknown message %s", event.provider_message_id)
        return

    # Forward-only: providers deliver receipts out of order (and retry old
    # ones), and "read" must never regress to "delivered".
    if status_rank(event.status.value) <= status_rank(message.status):
        return
    message.status = event.status.value
    message.save(update_fields=["status"])


def _upsert_contact(phone: str, contact_name: str, channel: str) -> Client:
    """Find the Client for a phone number, creating one on first contact.

    ``phone`` is whichever side of the event is the customer -- ``from_number``
    for an inbound message, ``to_number`` for one of ours sent outside the
    Inbox (see :func:`_apply_outbound_event`)."""
    contact = Client.objects.filter(phone=phone).first()
    if contact is not None:
        return contact

    name = contact_name.strip() or phone
    return Client.objects.create(
        first_name=name[:80],
        phone=phone,
        channel=_client_channel(channel),
        # +57 numbers get the Colombian flag in the CRM table; other prefixes
        # are left blank rather than guessed.
        country="CO" if phone.startswith("+57") else "",
    )


def _client_channel(conversation_channel: str) -> str:
    """Map a conversation channel key onto Client's coarser channel choices
    (the CRM table doesn't distinguish DM surfaces from the brand)."""
    if conversation_channel.startswith("tiktok"):
        return "tiktok"
    if conversation_channel == "instagram-dm":
        return "instagram"
    return conversation_channel


def _get_or_create_open_conversation(contact: Client, channel: str) -> Conversation:
    """The active thread for this contact+channel, or a fresh one.

    Only open/pending threads are reused. Resolved threads stay closed
    history -- a new inbound after resolution starts a new conversation row,
    so per-thread metrics survive the customer coming back.
    """
    conversation = (
        Conversation.objects.filter(contact=contact, channel=channel)
        .exclude(status=Conversation.RESOLVED)
        .order_by("-last_message_at")
        .first()
    )
    if conversation is not None:
        return conversation
    return Conversation.objects.create(contact=contact, channel=channel)


# --- Tags -------------------------------------------------------------------
#
# Every surface that touches tags -- the row picker, the chat header, bulk
# actions, the Etiquetas admin -- goes through these functions, so rules like
# "archived tags leave history alone" live in exactly one place.


class TagNameTaken(Exception):
    """A tag with this name (case-insensitively) already exists."""


def _validate_tag_name(name: str, exclude_pk=None) -> str:
    name = name.strip()
    if not name:
        raise ValueError("El nombre de la etiqueta no puede estar vacío.")
    clash = Tag.objects.filter(name__iexact=name)
    if exclude_pk is not None:
        clash = clash.exclude(pk=exclude_pk)
    if clash.exists():
        raise TagNameTaken(f"Ya existe una etiqueta llamada «{name}».")
    return name


def _validate_tag_color(color: str) -> str:
    valid = {key for key, _ in Tag.COLOR_CHOICES}
    if color not in valid:
        raise ValueError(f"Color desconocido: {color!r}")
    return color


def next_tag_color() -> str:
    """A palette token for a tag created without picking one (the picker's
    inline «Crear ...» path). Cycling by tag count spreads new tags across
    the palette instead of piling them onto one default; the color stays
    editable in the Etiquetas page."""
    palette = [key for key, _ in Tag.COLOR_CHOICES]
    return palette[Tag.objects.count() % len(palette)]


def create_tag(name: str, color: str | None = None, user=None) -> Tag:
    """Create a tag, enforcing the case-insensitive unique name.

    Raises :class:`TagNameTaken` on a duplicate and ``ValueError`` on an
    empty name or a color outside the preset palette. The DB constraint
    backs the name check up against races.
    """
    name = _validate_tag_name(name)
    color = _validate_tag_color(color) if color else next_tag_color()
    try:
        return Tag.objects.create(
            name=name,
            color=color,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
    except IntegrityError as exc:
        raise TagNameTaken(f"Ya existe una etiqueta llamada «{name}».") from exc


def update_tag(tag: Tag, name: str, color: str) -> Tag:
    """Rename/recolor a tag -- every pill referencing it updates at once,
    which is the point of tagging by FK instead of by string."""
    tag.name = _validate_tag_name(name, exclude_pk=tag.pk)
    tag.color = _validate_tag_color(color)
    tag.save(update_fields=["name", "color"])
    return tag


def set_tag_archived(tag: Tag, archived: bool) -> Tag:
    """Archive (or restore) a tag.

    Archiving is the only "delete": the tag vanishes from pickers and
    filters but every ConversationTag row survives, so tagged history stays
    exactly as it was. Never hard-delete a tag that is in use.
    """
    tag.is_archived = archived
    tag.save(update_fields=["is_archived"])
    return tag


def apply_tag(conversations, tag: Tag, user=None) -> int:
    """Apply ``tag`` to each conversation; returns how many actually gained it.

    Accepts any iterable/queryset of Conversations -- one row, or hundreds
    from a bulk action. Idempotent per conversation (already-tagged rows are
    skipped), and archived tags are refused: they exist only as history.
    """
    if tag.is_archived:
        raise ValueError("No se puede aplicar una etiqueta archivada.")

    tagged_by = user if getattr(user, "is_authenticated", False) else None
    applied = 0
    for conversation in conversations:
        _, created = ConversationTag.objects.get_or_create(
            conversation=conversation,
            tag=tag,
            defaults={"tagged_by": tagged_by},
        )
        applied += created
    return applied


def remove_tag(conversations, tag: Tag) -> int:
    """Take ``tag`` off each conversation; returns how many rows changed.

    Works on archived tags too -- an archived tag can still be *removed*
    from a chat (that's editing that chat), it just can't be newly applied.
    """
    deleted, _ = ConversationTag.objects.filter(
        conversation__in=conversations, tag=tag
    ).delete()
    return deleted


# --- Fake-provider pump -----------------------------------------------------


def pump_provider_events() -> None:
    """Let a pull-based provider (the fake one) deliver its pending events.

    Real providers push webhooks; the fake provider has no server to push
    from, so the Inbox poll endpoints call this instead. Events flow through
    :func:`process_inbound_events` exactly like a webhook's would. A no-op
    for providers without ``pending_status_events``.
    """
    provider = get_provider()
    pending = getattr(provider, "pending_status_events", None)
    if pending is None:
        return
    events = pending()
    if events:
        process_inbound_events(events)
