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

        ``last_inbound_at`` is denormalized bookkeeping this app maintains in
        ``services``, but rows also arrive from outside it (see the external
        writer contract in the README). A writer that inserts the message and
        forgets the UPDATE would otherwise leave the composer disabled
        forever, with nothing in the UI able to reopen it -- so a NULL falls
        back to asking the messages table. That costs one query, and only for
        the conversation actually being opened: the column still answers on
        its own whenever it is set, which is what keeps the list query flat.
        """
        last_inbound = self.last_inbound_at
        if last_inbound is None and self.pk is not None:
            last_inbound = (
                self.messages.filter(direction=Message.INBOUND)
                .order_by("-timestamp")
                .values_list("timestamp", flat=True)
                .first()
            )
        if last_inbound is None:
            return False
        return timezone.now() - last_inbound < SERVICE_WINDOW

    #: Shown when ``channel`` holds something this app has no brand mark for.
    UNKNOWN_CHANNEL_ICON = "icons/message-circle.svg"

    @property
    def icon_template(self) -> str:
        """Brand-mark template for this channel, for the conversation list.

        The two TikTok surfaces share one brand mark; everything else has a
        file named after its key (same files core.inbox.CANALES points at).

        An unrecognised value gets the generic mark instead of a path built
        from it. This used to interpolate the column straight into a template
        name, so a single row written with a channel like 'wa' or 'WhatsApp'
        raised TemplateDoesNotExist and took down the whole Inbox -- the list,
        the open thread and the poll -- for everyone, with no way to fix it
        short of editing the database.
        """
        if self.channel not in dict(self.CHANNEL_CHOICES):
            return self.UNKNOWN_CHANNEL_ICON
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

    #: The plantilla this message was sent from, when it was a template send
    #: (``services.send_template``). NULL for free-form messages and for
    #: everything that arrived over the webhook. SET_NULL rather than CASCADE:
    #: deleting a plantilla must never delete the messages sent with it, and
    #: the billing history below would go with them.
    template = models.ForeignKey(
        "core.MessageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
        verbose_name="plantilla",
    )

    # --- What this message cost -------------------------------------------
    #
    # WhatsApp bills every template message, by the plantilla's category and
    # the recipient's market (see messaging.pricing). These three are the
    # CRM's own estimate, frozen at send time and never recomputed: the price
    # list changes, and a rate change must not rewrite what last month cost.
    #
    # NULL means "not billed" -- every inbound message and every free-form
    # reply inside the 24h window. Zero means billed at nothing (a failed
    # send, or a rule that made it free), which is a different fact and stays
    # distinguishable.

    billed_category = models.CharField("categoría facturada", max_length=20, blank=True)
    #: Amount charged, in ``billed_currency``. Six decimals because
    #: per-message rates are quoted in fractions of a cent, and Decimal
    #: (never float) because 0.0008 has no exact binary form.
    billed_amount = models.DecimalField(
        "importe", max_digits=12, decimal_places=6, null=True, blank=True
    )
    billed_currency = models.CharField("moneda", max_length=3, blank=True)

    # --- What Meta says it cost -------------------------------------------
    #
    # The platform's own verdict, filled in when its delivery receipt arrives
    # (``services._apply_status_event``). Meta charges on *delivery*, not on
    # send, and at the category *it* assigned the plantilla -- it
    # re-categorises templates on its own -- so this is what corrects the
    # estimate above. Blank until a provider reports billing, which today
    # means every provider but Meta.

    #: ``regular`` (billable), ``free_customer_service`` or
    #: ``free_entry_point`` -- Meta's preferred billable test.
    meta_pricing_type = models.CharField(
        "tipo de precio (Meta)", max_length=32, blank=True
    )
    #: The rate Meta actually applied, stored verbatim (hyphen and all).
    meta_pricing_category = models.CharField(
        "categoría facturada (Meta)", max_length=32, blank=True
    )
    #: ``PMP`` (per-message, default since 2025-07-01) or ``CBP`` (legacy).
    meta_pricing_model = models.CharField(
        "modelo de precio (Meta)", max_length=8, blank=True
    )
    #: Meta's own billable flag; NULL until reported.
    meta_billable = models.BooleanField("facturable (Meta)", null=True, blank=True)

    class Meta:
        verbose_name = "mensaje"
        verbose_name_plural = "mensajes"
        # Chronological -- the order a chat thread renders in.
        ordering = ["timestamp", "id"]
        indexes = [
            # The thread query: one conversation's messages in order.
            models.Index(fields=["conversation", "timestamp"]),
            # The spend query (messaging.pricing.spent_between): billed rows
            # in a date range. Partial, because billed messages are the small
            # minority of a busy inbox.
            models.Index(
                fields=["timestamp"],
                condition=models.Q(billed_amount__isnull=False),
                name="message_billed_timestamp_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.direction}] {self.body[:40]}"

    @property
    def cost_is_confirmed(self) -> bool:
        """Whether ``billed_amount`` is Meta's verdict or still the CRM's
        pre-send estimate. False for every send through a provider that
        reports no billing (all of them but Meta today)."""
        return bool(self.meta_pricing_type or self.meta_pricing_category)

    @property
    def billed_category_display(self) -> str:
        """The Spanish label for what this send was billed as.

        Not ``get_billed_category_display()``: the field carries no choices
        on purpose (it records whatever category the plantilla had at send
        time, and a category Meta adds later must still store), so the labels
        come from the plantilla model -- imported inside the property to keep
        this module free of a top-level ``core.models`` import.
        """
        from core.models import MessageTemplate

        return dict(MessageTemplate.CATEGORY_CHOICES).get(
            self.billed_category, self.billed_category
        )

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
        """Tick/clock/alert icon for the outbound status indicator.

        Total on purpose. This was a bare dict subscript, so one row carrying
        a status from someone else's vocabulary ('accepted', 'error', '') --
        which is exactly what an external writer inserts (see the README's
        contract) -- raised KeyError and 500'd that entire conversation:
        the full page, the swap and the five-second poll alike.

        The fallback is the alert icon rather than a tick: an unrecognised
        state is not a delivery anyone confirmed, and drawing a tick would
        claim one.
        """
        return {
            MessageStatus.QUEUED.value: "icons/clock.svg",
            MessageStatus.SENT.value: "icons/check.svg",
            MessageStatus.DELIVERED.value: "icons/check-check.svg",
            MessageStatus.READ.value: "icons/check-check.svg",
            MessageStatus.FAILED.value: "icons/alert-circle.svg",
        }.get(self.status, "icons/alert-circle.svg")
