"""Conversations and messages -- the data behind the Inbox screen.

The contact side reuses :class:`core.models.Client`: the person you chat with
in the Inbox *is* the person in the CRM's Clientes table. Webhook processing
upserts Clients by phone number (see ``services.process_inbound_events``).
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .providers.types import MEDIA_PLACEHOLDERS, MessageStatus

#: How long after a customer's last message WhatsApp allows free-form replies.
#: Outside it only pre-approved templates may be sent -- a platform rule, so
#: it is enforced in ``services.send_message``, not left to the UI.
SERVICE_WINDOW = timedelta(hours=24)


class Tag(models.Model):
    """A user-created label for conversations ("CLIENTE NUEVO", "VENTA
    EFECTIVA"...).

    Tags are runtime data, not code: users invent them, name them and pick a
    color in the Etiquetas page -- no choices list to edit, no migration to
    run. Colors are palette *tokens* rather than raw hex: each token maps to
    a --tag-<token>-bg/-fg CSS variable pair (static/css/tags.css), so every
    pill stays readable and a future dark theme restyles all tags at once.

    Deletion is archiving: an archived tag disappears from pickers but stays
    on every conversation it was ever applied to. Removing a tag from 500
    chats' history because someone tidied the picker would silently rewrite
    the past.
    """

    # The full preset palette. Users pick from these swatches only --
    # arbitrary hex is how unreadable pills happen.
    COLOR_CHOICES = [
        ("green", "Verde"),
        ("yellow", "Amarillo"),
        ("purple", "Morado"),
        ("blue", "Azul"),
        ("red", "Rojo"),
        ("orange", "Naranja"),
        ("teal", "Turquesa"),
        ("pink", "Rosado"),
        ("indigo", "Índigo"),
        ("gray", "Gris"),
    ]

    name = models.CharField("nombre", max_length=40)
    color = models.CharField(
        "color", max_length=10, choices=COLOR_CHOICES, default="gray"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tags_created",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    is_archived = models.BooleanField("archivada", default=False)

    class Meta:
        verbose_name = "etiqueta"
        verbose_name_plural = "etiquetas"
        ordering = ["name"]
        constraints = [
            # Case-insensitive: "ventas" and "VENTAS" are the same label.
            # TODO(tenancy): there is no organization/account model yet, so
            # names are globally unique. When tenancy lands, add the org FK
            # here and scope this constraint to (org, Lower(name)).
            models.UniqueConstraint(Lower("name"), name="tag_name_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class Conversation(models.Model):
    """One thread with one client on one channel.

    A client can have several rows over time (a resolved WhatsApp thread and a
    fresh one; a WhatsApp and an Instagram thread in parallel) but only one
    *active* thread per channel -- ``process_inbound_events`` appends to an
    open/pending conversation before it ever creates a new one.
    """

    # Keys deliberately match core.inbox.CANALES filter keys, so an inbox
    # channel filter is a straight ``channel=key`` lookup. (core.models.Client
    # keeps its own coarser channel field for the CRM table; this one says
    # where *this thread* lives.)
    CHANNEL_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("messenger", "Messenger"),
        ("instagram-dm", "Instagram DM"),
        ("facebook", "Facebook"),
        ("instagram", "Instagram"),
        ("tiktok-dm", "Tiktok DM"),
        ("tiktok-coment", "Tiktok comentarios"),
    ]

    OPEN, PENDING, RESOLVED = "open", "pending", "resolved"
    STATUS_CHOICES = [
        (OPEN, "Abierta"),
        (PENDING, "Por resolver"),
        (RESOLVED, "Resuelta"),
    ]

    contact = models.ForeignKey(
        "core.Client",
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name="cliente",
    )
    channel = models.CharField(
        "canal", max_length=20, choices=CHANNEL_CHOICES, default="whatsapp"
    )
    status = models.CharField(
        "estado", max_length=10, choices=STATUS_CHOICES, default=OPEN
    )

    # NULL = "Sin asignar" in the inbox nav; set = it shows in that user's
    # "Tu inbox".
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        verbose_name="asignada a",
    )

    #: Timestamp of the newest message either direction -- the list sort key.
    last_message_at = models.DateTimeField(null=True, blank=True)

    #: Timestamp of the newest *inbound* message. Denormalized (maintained by
    #: ``services``) so the 24h-window check and the conversation list don't
    #: re-query the messages table per row.
    last_inbound_at = models.DateTimeField(null=True, blank=True)

    unread_count = models.PositiveIntegerField(default=0)

    # Through-model rather than a bare M2M so every application of a tag
    # records who and when (see ConversationTag) -- auditable tagging.
    tags = models.ManyToManyField(
        Tag,
        through="ConversationTag",
        related_name="conversations",
        blank=True,
        verbose_name="etiquetas",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "conversación"
        verbose_name_plural = "conversaciones"
        # Newest activity first -- the conversation list's natural order.
        ordering = ["-last_message_at"]
        indexes = [
            # The list query: filter by status, order by recency.
            models.Index(fields=["status", "last_message_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.contact} · {self.get_channel_display()}"

    @property
    def is_within_24h_window(self) -> bool:
        """Whether WhatsApp's customer-service window is currently open.

        The window opens (and re-opens) with every *inbound* message. A
        conversation the customer has never written to -- or not in 24h --
        gets ``False``: only a template send may (re)start it.
        """
        if self.last_inbound_at is None:
            return False
        return timezone.now() - self.last_inbound_at < SERVICE_WINDOW

    @property
    def icon_template(self) -> str:
        """Brand-mark template for this channel, for the conversation list.

        The two TikTok surfaces share one brand mark; everything else has a
        file named after its key (same files core.inbox.CANALES points at).
        """
        name = "tiktok" if self.channel.startswith("tiktok") else self.channel
        return f"icons/brands/{name}.svg"


class ConversationTag(models.Model):
    """One tag on one conversation, with the audit trail of its application.

    This is the M2M through table -- explicit so "who tagged this chat, and
    when" is answerable, which a plain ManyToManyField throws away.
    """

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="conversation_tags"
    )
    tag = models.ForeignKey(
        Tag, on_delete=models.CASCADE, related_name="conversation_tags"
    )

    tagged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tags_applied",
    )
    tagged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "etiqueta de conversación"
        verbose_name_plural = "etiquetas de conversación"
        constraints = [
            # The same tag on the same chat twice means nothing -- one row.
            models.UniqueConstraint(
                fields=["conversation", "tag"], name="conversationtag_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tag} → {self.conversation}"


class Message(models.Model):
    """One message inside a conversation, either direction."""

    INBOUND, OUTBOUND = "inbound", "outbound"
    DIRECTION_CHOICES = [(INBOUND, "Entrante"), (OUTBOUND, "Saliente")]

    STATUS_CHOICES = [(s.value, s.value) for s in MessageStatus]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    direction = models.CharField(max_length=8, choices=DIRECTION_CHOICES)

    body = models.TextField(blank=True)
    media_url = models.URLField(blank=True)

    #: Kind of attachment behind ``media_url`` (``image``, ``video``,
    #: ``audio``, ``document``, ``sticker`` -- see ``InboundEvent.media_type``).
    #: Decides whether the thread renders the media inline or as a download
    #: link. Blank for text-only messages and for rows from before the field
    #: existed (those keep the generic link).
    media_type = models.CharField(max_length=16, blank=True)

    #: Delivery lifecycle; only meaningful outbound (inbound rows are stored
    #: as ``delivered`` -- they reached us, by definition).
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=MessageStatus.QUEUED.value
    )

    #: The provider's id -- unique so webhook retries can't insert the same
    #: message twice (the DB backs up the application-level check), and what
    #: status callbacks use to find their message. NULL for outbound rows
    #: whose send failed before the provider assigned an id.
    provider_message_id = models.CharField(
        max_length=255, unique=True, null=True, blank=True
    )

    #: Provider-reported time when available, receipt/send time otherwise.
    timestamp = models.DateTimeField(default=timezone.now)

    #: Who wrote it, for outbound messages sent from the Inbox.
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
    )

    class Meta:
        verbose_name = "mensaje"
        verbose_name_plural = "mensajes"
        # Chronological -- the order a chat thread renders in.
        ordering = ["timestamp", "id"]
        indexes = [
            # The thread query: one conversation's messages in order.
            models.Index(fields=["conversation", "timestamp"]),
        ]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.body[:40]}"

    @property
    def is_outbound(self) -> bool:
        return self.direction == self.OUTBOUND

    @property
    def is_inline_image(self) -> bool:
        """Whether the thread should show the media itself rather than a
        download link. Stickers are WebP -- browsers render them natively."""
        return bool(self.media_url) and self.media_type in ("image", "sticker")

    @property
    def display_body(self) -> str:
        """Body for the chat bubble. When the image itself renders inline,
        the "[imagen]"/"[sticker]" placeholder would just repeat it as text --
        real captions still show. The raw ``body`` keeps the placeholder so
        the conversation list preview reads as something."""
        if self.is_inline_image and self.body == MEDIA_PLACEHOLDERS.get(self.media_type):
            return ""
        return self.body

    @property
    def status_icon_template(self) -> str:
        """Tick/clock/alert icon for the outbound status indicator."""
        return {
            MessageStatus.QUEUED.value: "icons/clock.svg",
            MessageStatus.SENT.value: "icons/check.svg",
            MessageStatus.DELIVERED.value: "icons/check-check.svg",
            MessageStatus.READ.value: "icons/check-check.svg",
            MessageStatus.FAILED.value: "icons/alert-circle.svg",
        }[self.status]
