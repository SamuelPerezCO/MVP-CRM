"""Application services: everything between the UI/webhook and the provider.

Three entry points matter:

* :func:`process_inbound_events` -- the "heavy" half of webhook handling,
  kept out of the view so it can move behind a task queue (Celery/RQ) later
  without touching the endpoint: the view would enqueue the normalized events
  instead of calling this directly. There is no queue today (requirements.txt
  is Django only), so it runs synchronously.
* :func:`send_message` -- the only way the app sends a free-form text. It is
  where the 24-hour window rule lives.
* :func:`send_template` -- the only way the app sends one of its own
  plantillas, which is how a *new* client (nobody has written to us, so the
  window was never open) gets reached at all. Where ``send_message`` has the
  window rule, this one has the money: it prices the send, enforces the
  monthly ceiling and freezes what it cost onto the row.

The plantilla path end to end: the Enviar plantilla dialog POSTs to
``core.views.plantilla_send``, which finds or opens the thread
(:func:`find_open_conversation` / :func:`conversation_for_client`) and calls
:func:`send_template`; that function prices the send with
``messaging.pricing``, writes the ``Message`` row and hands the actual
delivery to whichever provider ``settings.MESSAGING_PROVIDER`` names
(``providers/registry.py``). :func:`sendable_templates` is the list the
dialog's plantilla picker is built from.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from core import plantillas
from core.models import Client, MessageTemplate

from . import pricing
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


# Raised by send_template's first two checks, before any Message row exists.
# core.views.plantilla_send catches it (together with BudgetExceeded) and
# shows str(exc) as the dialog's error line, which is why the message text is
# a Spanish sentence for the agent rather than a developer note.
class TemplateNotSendable(Exception):
    """The plantilla itself can't go out: switched off, or rejected by
    WhatsApp. Raised before anything is billed or written."""


class BudgetExceeded(Exception):
    """The send would push this month past ``MESSAGING_MONTHLY_BUDGET``.

    Template sends cost real money, so the ceiling is enforced here rather
    than trusted to the UI -- a bulk loop and a hand-crafted POST hit the
    same guard the dialog does.

    Raised by :func:`send_template` after pricing and before the Message row
    is written, so a refused send leaves no trace in the thread.
    ``core.views.plantilla_send`` catches it and shows ``str(exc)`` (a
    Spanish sentence naming the ceiling) as the dialog's error line.
    """


# --- Sending ---------------------------------------------------------------


def send_message(conversation: Conversation, body: str, user=None) -> Message:
    """Send a free-form text in ``conversation`` and record it.

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
        status=MessageStatus.QUEUED.value,
        sent_by=user if getattr(user, "is_authenticated", False) else None,
    )

    provider = get_provider()
    try:
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


def conversation_for_client(client: Client, channel: str = "whatsapp") -> Conversation:
    """The thread a message to ``client`` belongs in, created if there is none.

    This is what makes writing to a *new* client possible at all: someone
    added by hand in the CRM (or imported from a list) has never written, so
    no conversation exists to open in the Inbox. Reuses the same
    open-or-create rule inbound messages follow, so a template send and the
    customer's eventual reply land in one thread rather than two.

    Called by ``core.views.plantilla_send`` only once a send is really about
    to happen (the dialog itself uses :func:`find_open_conversation`, which
    never creates) and by the tests. ``channel`` defaults to WhatsApp because
    plantillas are a WhatsApp mechanism. Delegates to
    :func:`_get_or_create_open_conversation`: an open or pending thread on
    that channel is returned as is; otherwise a new ``Conversation`` row is
    inserted with the model defaults (status ``open``, no messages, no
    ``last_message_at`` yet).
    """
    return _get_or_create_open_conversation(client, channel)


def send_template(
    conversation: Conversation, template, values: dict | None = None, user=None
) -> Message:
    """Send one of the CRM's own plantillas, and record what it cost.

    The counterpart to :func:`send_message`: no 24-hour window check, because
    a template is precisely what the platform allows outside the window --
    the way to reach a client who has never written. What replaces that check
    is money. Every template send is billed, so this function:

    * refuses a plantilla that is switched off or rejected
      (:class:`TemplateNotSendable`) before anything is charged;
    * prices the send (``messaging.pricing.quote``) and refuses it when it
      would break the configured monthly ceiling (:class:`BudgetExceeded`);
    * freezes the price onto the Message row, so a later rate change cannot
      rewrite what this send cost.

    ``values`` maps variable number -> text ({1: "Camila"}); anything missing
    falls back to the plantilla's stored sample. Failure zeroes the amount
    rather than deleting the row: nothing is billed for a message that never
    left, and the thread still shows the attempt.

    Inputs: ``conversation`` is the thread the message lands in (from
    :func:`conversation_for_client`), ``template`` a
    ``core.models.MessageTemplate``, ``values`` the agent's variable texts
    keyed by int, ``user`` the request's user (stored as ``sent_by`` when
    authenticated). Returns the saved ``Message`` row. Called by
    ``core.views.plantilla_send`` (the dialog's POST) and by the tests; the
    provider it talks to is whichever ``settings.MESSAGING_PROVIDER`` names.
    """
    # 1. Refuse before anything is billed or written. Both checks read
    #    fields already on the template instance -- no query, no row.
    #    is_active is the account's own on/off toggle; status is WhatsApp's
    #    verdict, one of MessageTemplate.STATUS_CHOICES
    #    (pendiente/aceptada/rechazada).
    if not template.is_active:
        raise TemplateNotSendable(
            f"La plantilla «{template.name}» está desactivada."
        )
    if template.status == "rechazada":
        raise TemplateNotSendable(
            f"WhatsApp rechazó la plantilla «{template.name}»: no se puede enviar."
        )

    # 2. Render the text and price the send. fill_body swaps each {{n}} in
    #    the body for values[n], falling back to the plantilla's stored
    #    sample for that variable. The quote depends on the template's
    #    category, the client's country and whether this thread's 24h window
    #    is open (a utility template inside the window is free). A
    #    brand-new thread has no inbound message yet, so its window reads as
    #    closed and the full rate applies.
    body = plantillas.fill_body(template, values)
    quote = pricing.quote(
        template, conversation.contact, window_open=conversation.is_within_24h_window
    )
    # 3. The monthly ceiling. would_exceed_budget sums the billed_amount of
    #    this month's Message rows and adds quote.amount; with no
    #    MESSAGING_MONTHLY_BUDGET configured it is always False. Nothing
    #    locks between this check and the insert below, so two sends racing
    #    each other can both pass.
    if pricing.would_exceed_budget(quote.amount):
        raise BudgetExceeded(
            "Este envío supera el presupuesto mensual de plantillas "
            f"({pricing.budget()} {quote.currency}). Súbelo o espera al "
            "próximo mes."
        )

    # 4. Write the row before talking to the provider, same as send_message:
    #    a crash mid-send leaves a ``queued`` row as evidence instead of a
    #    lost message. objects.create() INSERTs immediately and returns the
    #    instance with its pk. The three billed_* columns are copied from the
    #    quote here and never recomputed -- that is what "frozen" means.
    #    sent_by is None when there is no user or an anonymous one:
    #    AnonymousUser.is_authenticated is False, and getattr() falls back to
    #    False when the object has no such attribute at all.
    message = Message.objects.create(
        conversation=conversation,
        direction=Message.OUTBOUND,
        body=body,
        status=MessageStatus.QUEUED.value,
        sent_by=user if getattr(user, "is_authenticated", False) else None,
        template=template,
        billed_category=quote.category,
        billed_amount=quote.amount,
        billed_currency=quote.currency,
    )

    # 5. Hand the send to the active provider. get_provider() instantiates
    #    the class registered under settings.MESSAGING_PROVIDER (always
    #    ``fake`` while the test runner is active, otherwise whatever the
    #    MESSAGING_PROVIDER environment variable names).
    provider = get_provider()
    # The provider contract (providers/base.py) wants the variables keyed by
    # their number as strings ({"1": "Andrés"}). The keys arrive as ints
    # (core.views._posted_values builds them with int()), hence str(); entries
    # whose text is empty are dropped.
    params = {str(number): text for number, text in (values or {}).items() if text}
    # Reserved key: the template's language code (es, en_US...). The Meta
    # provider puts it in its payload as template.language.code; Baileys
    # discards it.
    params["_language"] = template.language
    # The rendered text, for providers with no template mechanism of their
    # own (Baileys); ones with a real template API drop it.
    params["_body"] = body
    # send_template returns the provider's own message id (a string) on
    # success and raises on anything else; that id is all we need back.
    try:
        provider_id = provider.send_template(
            to=conversation.contact.phone, template_name=template.name, params=params
        )
    except Exception as exc:
        # 6a. Any provider error (network, HTTP, a bug in the adapter) lands
        #     here: the row stays, marked failed, so the thread shows the
        #     attempt.
        message.status = MessageStatus.FAILED.value
        # Nothing is charged for a message the provider never accepted.
        message.billed_amount = Decimal("0")
        # Zero, not NULL: NULL means "never billed" (inbound rows, free-form
        # replies), while 0 keeps this row inside pricing.spent_between's
        # billed_amount__isnull=False filter and simply adds nothing. Decimal
        # rather than float because the column is a DecimalField and binary
        # floats cannot hold amounts like 0.0125 exactly.
        # save(update_fields=...) issues an UPDATE on just these two columns.
        message.save(update_fields=["status", "billed_amount"])
        # logger.exception records the traceback at ERROR level; "from exc"
        # chains the provider's error onto the SendFailed the view catches.
        logger.exception("send_template failed for conversation %s", conversation.pk)
        raise SendFailed(str(exc)) from exc

    # 6b. Success: attach the provider's id. It is the key later status
    #     webhooks (_apply_status_event) use to find this row and move it
    #     from queued to sent/delivered/read.
    message.provider_message_id = provider_id
    message.save(update_fields=["provider_message_id"])

    # 7. Bump the thread so it sorts to the top of the Inbox list
    #    (Conversation.Meta.ordering is -last_message_at). Only
    #    last_message_at moves: last_inbound_at is left alone, so our own
    #    send does not open the 24h window -- only the customer writing back
    #    does that.
    conversation.last_message_at = message.timestamp
    conversation.save(update_fields=["last_message_at"])
    return message


#: Meta's status vocabulary is wider than the CRM's three-value ``status``
#: field, so the extra verdicts collapse onto "pendiente" -- which the UI
#: reads as "not usable yet", true of every one of them. The raw value is
#: kept in ``MessageTemplate.meta_status``, and that is what decides whether
#: a plantilla may actually be sent (only APPROVED may).
_META_STATUS_TO_LOCAL = {
    "APPROVED": "aceptada",
    "REJECTED": "rechazada",
}


def sync_templates() -> dict:
    """Pull the WABA's templates from Meta and reconcile the CRM's rows.

    Two things only Meta knows, and both cost money to get wrong:

    * **Which plantillas may actually send.** Only an APPROVED template can
      be delivered; a PAUSED or DISABLED one is refused at the API. Without
      this the send dialog offers plantillas WhatsApp would reject.
    * **The category Meta assigned.** Meta re-categorises templates on its
      own -- a utility template it judges promotional becomes marketing --
      and bills at *its* category. Syncing that keeps the quote honest
      before the send, instead of finding out from the delivery receipt.

    Rows are matched on (name, language), which is Meta's own key for a
    template and this model's unique constraint. A template that exists in
    Meta but not here (created in WhatsApp Manager) is imported, so the
    Plantillas table shows the whole account; a row that exists only here is
    left alone and reported as ``unmatched`` -- it may simply not have been
    submitted to Meta yet, and deleting a user's draft because Meta has not
    heard of it would be wrong.

    Returns a small report ``{"fetched", "updated", "created", "unmatched",
    "recategorised"}`` for the management command to print. Raises whatever
    the provider raises when Meta is unreachable or unconfigured: a sync
    that half-failed should be visible, not silent.
    """
    provider = get_provider()
    fetch = getattr(provider, "fetch_templates", None)
    if fetch is None:
        raise RuntimeError(
            f"the {provider.name!r} provider cannot list templates -- "
            "set MESSAGING_PROVIDER=meta"
        )

    rows = fetch()
    report = {
        "fetched": len(rows),
        "updated": 0,
        "created": 0,
        "unmatched": 0,
        "recategorised": [],
    }
    now = timezone.now()
    seen = set()

    for row in rows:
        name = (row.get("name") or "").strip()
        language = (row.get("language") or "").strip()
        if not name or not language:
            logger.info("meta template without name/language, skipped: %r", row)
            continue
        seen.add((name, language))

        meta_status = (row.get("status") or "").strip().upper()
        # Meta's categories are upper case (MARKETING); the CRM stores them
        # lower case, and only knows the three a plantilla can be created
        # with. Anything else (FREE_SERVICE and whatever Meta adds next) is
        # recorded as a status change but leaves the category alone rather
        # than writing a value the editor and pricing cannot read.
        meta_category = (row.get("category") or "").strip().lower()
        if meta_category not in pricing.CATEGORIES:
            meta_category = ""

        template = MessageTemplate.objects.filter(name=name, language=language).first()
        if template is None:
            # Imported from WhatsApp Manager. The body stays empty: this
            # endpoint returns `components`, but rebuilding the editor's
            # body/samples/buttons from them is a translation this sync does
            # not do -- the row exists so the account is visible and its
            # status is right, and the editor can fill it in.
            MessageTemplate.objects.create(
                name=name,
                language=language,
                category=meta_category or "marketing",
                status=_META_STATUS_TO_LOCAL.get(meta_status, "pendiente"),
                meta_template_id=str(row.get("id") or ""),
                meta_status=meta_status,
                meta_synced_at=now,
            )
            report["created"] += 1
            continue

        changed = []
        for field, value in (
            ("meta_template_id", str(row.get("id") or "")),
            ("meta_status", meta_status),
            ("status", _META_STATUS_TO_LOCAL.get(meta_status, "pendiente")),
        ):
            if getattr(template, field) != value:
                setattr(template, field, value)
                changed.append(field)

        # A category change is worth reporting, not just applying: it moves
        # what every future send of this plantilla costs.
        if meta_category and template.category != meta_category:
            report["recategorised"].append(
                {"name": name, "from": template.category, "to": meta_category}
            )
            logger.info(
                "meta re-categorised plantilla %s: %s -> %s",
                name, template.category, meta_category,
            )
            template.category = meta_category
            changed.append("category")

        template.meta_synced_at = now
        changed.append("meta_synced_at")
        template.save(update_fields=changed)
        if len(changed) > 1:  # more than the timestamp
            report["updated"] += 1

    # Rows the account does not have. Counted, never touched.
    report["unmatched"] = sum(
        1
        for name, language in MessageTemplate.objects.values_list("name", "language")
        if (name, language) not in seen
    )
    return report


def sendable_templates():
    """The plantillas the send dialog offers, best first.

    Same stance as the Inbox's Respuestas rápidas picker: every active
    plantilla that WhatsApp hasn't rejected, pendientes included. A real
    account can only send *aceptada* ones, but this MVP has no approval
    pipeline, so filtering them out would leave every freshly created
    plantilla unusable. The UI badges them instead of hiding them.

    Returns a lazy QuerySet: no query runs until it is iterated (the views
    wrap it in ``list()``). Called by ``core.views._send_form_context`` to
    fill the picker, and by ``plantilla_send_form`` / ``plantilla_send`` to
    check that the id a request names is one the picker actually offered.
    ``filter`` and ``exclude`` become a single SQL WHERE
    (``is_active AND NOT status = 'rechazada'``).
    """
    return (
        MessageTemplate.objects.filter(is_active=True)
        # Once a plantilla has been synced with Meta, Meta decides: only
        # APPROVED can be delivered, so a PAUSED or DISABLED one drops out of
        # the picker instead of being offered for a send WhatsApp refuses. A
        # plantilla Meta has never seen (meta_status blank -- no Meta account,
        # or created here and not submitted) keeps the lenient MVP rule.
        .filter(
            models.Q(meta_status="APPROVED")
            | (models.Q(meta_status="") & ~models.Q(status="rechazada"))
        )
        # "aceptada" sorts before "pendiente" alphabetically, which is also
        # the order they should be offered in -- approved plantillas first.
        .order_by("status", "name")
    )


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
    """A delivery receipt for a message we sent: move its status forward, and
    record what the platform says it charged."""
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

    changed = []

    # Billing first, and deliberately outside the forward-only rule below.
    # Meta puts its pricing object on the ``sent`` receipt and on one of
    # delivered/read, and those can arrive in any order; a late ``sent``
    # still carries a verdict worth recording even though it cannot move the
    # status forward.
    if event.pricing:
        changed += _apply_pricing(message, event.pricing)

    # Forward-only: providers deliver receipts out of order (and retry old
    # ones), and "read" must never regress to "delivered".
    if status_rank(event.status.value) > status_rank(message.status):
        message.status = event.status.value
        changed.append("status")

    if changed:
        message.save(update_fields=changed)


def _apply_pricing(message: Message, pricing_info: dict) -> list[str]:
    """Record the platform's billing verdict on ``message`` and correct the
    amount the CRM estimated. Returns the fields it changed.

    The estimate frozen at send time is the best the CRM can do beforehand;
    this is what actually happened, and it can differ three ways:

    * **Free after all.** ``type`` is ``free_customer_service`` or
      ``free_entry_point`` (or ``billable`` is false) -- the send rode an
      open window the CRM did not know about, most often the 72-hour free
      entry point that follows a Click-to-WhatsApp ad. The amount drops to
      zero.
    * **A different category.** Meta re-categorises templates on its own
      (a utility template judged to be marketing, say) and bills at *its*
      category. The amount is recomputed from that, at the rate card in
      force when the message was sent -- not today's card, so a message
      priced under an old card stays priced under it.
    * **Nothing to change.** The common case: Meta confirms the category and
      that it was billable, and the amount already says so.

    Never touches a failed message: nothing is charged for a send that did
    not arrive, and ``send_template`` has already zeroed it.
    """
    fields = []

    def record(name, value):
        if getattr(message, name) != value:
            setattr(message, name, value)
            fields.append(name)

    record("meta_pricing_type", pricing_info.get("type") or "")
    record("meta_pricing_category", pricing_info.get("category") or "")
    record("meta_pricing_model", pricing_info.get("model") or "")
    billable = pricing_info.get("billable")
    record("meta_billable", billable if isinstance(billable, bool) else None)

    # Only messages the CRM actually billed are worth reconciling: an
    # inbound row, or a free-form reply, carries NULL and stays NULL.
    if message.billed_amount is None or message.status == MessageStatus.FAILED.value:
        return fields

    pricing_type = (pricing_info.get("type") or "").strip()
    # ``type`` is absent from some payloads, so fall back to ``billable``;
    # with neither, assume Meta charged (the estimate stands).
    if pricing_type:
        is_free = pricing_type != "regular"
    elif isinstance(billable, bool):
        is_free = not billable
    else:
        is_free = False

    if is_free:
        if message.billed_amount != Decimal("0"):
            message.billed_amount = Decimal("0")
            fields.append("billed_amount")
        return fields

    # Billable, at Meta's category. Re-price only when that differs from
    # what the CRM assumed, and only for a category the rate card knows --
    # marketing_lite and referral_conversion are billed by mechanisms this
    # CRM does not implement, so their amounts are left as they are.
    category = (pricing_info.get("category") or "").strip()
    if not category or category == message.billed_category:
        return fields
    if category not in pricing.CATEGORIES:
        logger.info(
            "meta billed message %s as %r, which this CRM cannot price",
            message.provider_message_id,
            category,
        )
        return fields

    market = pricing.market_for(message.conversation.contact)
    # timezone.localdate() of the send, so an old message keeps the card it
    # was actually billed under.
    amount = pricing.rate_for(market, category, timezone.localdate(message.timestamp))
    if amount != message.billed_amount:
        logger.info(
            "meta re-categorised message %s from %s to %s: %s -> %s",
            message.provider_message_id,
            message.billed_category or "(none)",
            category,
            message.billed_amount,
            amount,
        )
        message.billed_amount = amount
        fields.append("billed_amount")
    if message.billed_category != category:
        message.billed_category = category
        fields.append("billed_category")
    return fields


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


def find_open_conversation(
    contact: Client, channel: str = "whatsapp"
) -> Conversation | None:
    """The active thread for this contact+channel, or ``None``.

    Only open/pending threads count. Resolved ones stay closed history -- a
    new inbound after resolution starts a new conversation row, so per-thread
    metrics survive the customer coming back.

    Read-only on purpose, and public for it: the send dialog needs to know
    whether a thread (and so a 24h window) exists before deciding what to
    charge, and merely *looking* must not create one.

    Called by ``core.views._send_form_context`` (to price the dialog) and
    ``core.views.plantilla_send`` (to know whether the send is opening a
    thread), and by :func:`_get_or_create_open_conversation` below. Query
    mechanics: ``filter`` picks this contact's threads on this channel,
    ``exclude`` drops the resolved ones (open and pending stay),
    ``order_by`` puts the most recent activity first and ``first()`` runs
    the query with ``LIMIT 1``, returning that row or ``None``.
    """
    return (
        Conversation.objects.filter(contact=contact, channel=channel)
        .exclude(status=Conversation.RESOLVED)
        .order_by("-last_message_at")
        .first()
    )


def _get_or_create_open_conversation(contact: Client, channel: str) -> Conversation:
    """The active thread for this contact+channel, or a fresh one."""
    # Look first, create only on a miss. The lookup is the public read-only
    # function above, so both apply the same "resolved threads don't count"
    # rule. Conversation.objects.create() inserts the row with the model
    # defaults: status open, unread_count 0, assigned_to NULL, and
    # last_message_at / last_inbound_at NULL until a message lands.
    conversation = find_open_conversation(contact, channel)
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
