"""What a template send costs, and what has been spent so far.

The 24-hour window rule has a price tag behind it. A free-form reply inside
the window is free; reaching someone *outside* it -- a client who has never
written, which is exactly the "clientes nuevos" case -- means sending an
approved template, and WhatsApp bills every one of those per message, priced
by the template's **category** and the recipient's **market** -- Meta's own
name for a country ("Colombia") or a regional bucket ("Rest of Latin
America") on its rate card.

So this module answers two questions, and nothing else touches them:

* :func:`quote` -- what will this particular send cost, before it happens.
  The UI shows it next to the Enviar button; ``services.send_template``
  freezes the answer onto the Message row (rates change, history must not).
* :func:`month_to_date` / :func:`budget` -- what has been spent this month,
  and the optional ceiling that stops the sending once it is reached.

The numbers are Meta's own. :mod:`messaging.meta_rates` holds the published
rate card -- transcribed from the CSVs Meta links from its pricing docs, both
the card in force and the one already announced -- and :func:`card_for` picks
by date, so a quote switches over on the day a new card takes effect.
``MESSAGING_TEMPLATE_RATES`` (JSON, see .env.example) still overlays it, per
market row, because a rate card is ultimately per *account*: a BSP contract,
a promotional rate or a currency other than USD.

Every quote is an **estimate**, and the code says so wherever it matters:
Meta charges when a template is *delivered*, not when it is sent, and it
bills at the category *it* has assigned the template. The authority is the
``pricing`` object Meta puts on the delivery webhook. Two known ways this
estimate errs, both deliberately upward: volume tiers (which discount utility
and authentication once a portfolio's monthly volume crosses a threshold this
CRM cannot see) and the free entry point window (72 free hours after replying
to a Click-to-WhatsApp ad, which needs the inbound ``referral`` object the
CRM does not record yet).

How the pieces connect, for a first read of this file:

* Two modules call in. ``core.views._send_form_context`` calls
  :func:`budget_state` every time the send dialog (send_form.html) is
  rendered, and :func:`quote` as soon as a plantilla is selected, so the
  agent sees the price before pressing Enviar.
  ``messaging.services.send_template`` calls :func:`quote` and
  :func:`would_exceed_budget` (and :func:`budget`, for the wording of the
  refusal) just before it creates the Message row, then copies the Quote's
  category/amount/currency onto that row. After a successful send
  ``core.views.plantilla_send`` calls :func:`budget_state` once more, so the
  confirmation shows the month's total including that send.
* Money is always :class:`decimal.Decimal`, never ``float``. A float cannot
  hold 0.0125 exactly, and thousands of per-message rates added up would
  drift; ``Message.billed_amount`` is a ``DecimalField`` for the same reason,
  so amounts travel between this module and the database unchanged.
* Nothing is cached. :func:`rates`, :func:`currency` and :func:`budget`
  re-read ``django.conf.settings`` on every call, so a value swapped in by
  ``override_settings`` in a test, or changed on the next deploy, takes
  effect at once.
* Only the functions under the "Spend" heading query the database. The
  pricing half is arithmetic over the shipped card plus the ``category`` of a
  MessageTemplate and the ``country``/``phone`` of a Client.
* The market resolution is where the money is won or lost: see
  :func:`market_for_phone` for the longest-prefix rule and the +1 trap that
  bills Dominican, Jamaican and Puerto Rican numbers at roughly three times
  the North American rate.
"""

# Stores the type hints in this file as strings instead of evaluating them
# at import time; they document the code and cost nothing at run time.
from __future__ import annotations

# json: MESSAGING_TEMPLATE_RATES arrives as a JSON string, parsed in rates().
# Decimal: the only numeric type used for money here (module docstring).
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

# ``settings`` is Django's lazy view of config/settings.py. Reading it as
# ``getattr(settings, "NAME", default)`` tolerates a name that was never
# defined there, and it sees values swapped in by ``override_settings`` in
# tests; nothing in this module caches what it reads, so those are honoured.
from django.conf import settings
# ``timezone`` gives timezone-aware datetimes: ``timezone.now()`` is the
# current instant in UTC, ``timezone.localtime()`` converts one to the
# active time zone (settings.TIME_ZONE unless ``timezone.activate()`` set
# another).
from django.utils import timezone

# The zone the whole app calls "today". core.calendario imports nothing from
# messaging, so this is a plain module-level import, not the deferred one
# services.py needs for core.plantillas.
from core.calendario import CALENDAR_TZ

# Meta's published rate card, market->country map and calling-code tables.
# Data only, generated from Meta's own CSV downloads -- see that module.
from . import meta_rates

# Named after the module ("messaging.pricing"), so log filtering can single
# out the two "ignoring malformed ..." messages this file emits.
logger = logging.getLogger(__name__)

# (Comments starting with ``#:`` document the constant right below them; it
# is the Sphinx convention this file uses for module-level values.)
#
# CATEGORIES is used two ways: quote() prices anything outside this tuple
# as "marketing", and rates() rejects an override naming a category that
# is not in it. "authentication-international" is deliberately absent: it is
# a *rate column* on Meta's card, not a category a plantilla can be created
# with, and it only applies to accounts sending more than 750K out-of-window
# messages in 30 days (see meta_rates.AUTHENTICATION_INTERNATIONAL_MARKETS).
#: The billable categories -- the same keys as
#: ``core.models.MessageTemplate.CATEGORY_CHOICES``, because a template's
#: category *is* what it is billed as.
CATEGORIES = ("marketing", "utility", "authentication")

# Meta's card has no "everything else" row in the sense a default usually
# means -- "Other" is a real row on it ("All other countries"), and it is
# where a number we cannot place lands.
#: The market that prices a recipient we cannot resolve to a country.
DEFAULT_MARKET = "Other"

#: The zone the billing month is bucketed in. Deliberately NOT
#: settings.TIME_ZONE (UTC): every other "today" in this app is the Bogotá
#: wall clock -- core.calendario enters events in it and
#: core.estadisticas_volumen buckets its days in it under the same reasoning,
#: "one app, one today". Billing has to agree, or a send at 20:00 on the last
#: day of the month falls into the next month's budget while the agent who
#: made it is still looking at the old one.
BILLING_TZ = CALENDAR_TZ


# ``@dataclass(frozen=True)`` writes __init__/__repr__/__eq__ from the field
# list in the class body and makes instances read-only: assigning to a
# field afterwards raises FrozenInstanceError, so a quote handed to a
# template or copied onto a Message row is exactly what quote() computed.
@dataclass(frozen=True)
class Quote:
    """What one template send is expected to be billed.

    ``unit_amount`` is Meta's list price for this category+market;
    ``amount`` is what this send actually costs, which is zero when a rule
    makes it free (``free_reason`` says which). Both travel to the UI, so
    the dialog can show "gratis" *and* what it would otherwise have cost.

    ``market`` is Meta's own name for the price row -- a country
    ("Colombia") or one of its regional buckets ("Rest of Latin America") --
    so what the dialog shows is the row the invoice will use.

    An estimate until the delivery receipt: Meta charges on delivery, at the
    category it has assigned the template. See :func:`quote`.
    """

    # Who reads what: send_form.html prints ``currency``, ``amount``,
    # ``unit_amount``, ``market`` and ``free_reason``; services.send_template
    # copies ``category``/``amount``/``currency`` onto the Message row as
    # billed_category/billed_amount/billed_currency.
    category: str
    market: str
    currency: str
    unit_amount: Decimal
    amount: Decimal
    free_reason: str = ""
    #: Priced at the market's Service rate rather than the category's own --
    #: a utility template inside an open window. ``services.send_template``
    #: copies it onto the Message row, which is what the monthly free
    #: allowance is counted from.
    billed_as_service: bool = False

    @property
    def is_free(self) -> bool:
        """True when nothing will be charged for this send.

        ``Decimal("0") == 0`` holds, so no conversion is needed. Templates
        read it as ``quote.is_free``: Django's dotted lookup resolves
        attributes, so a property needs no parentheses.
        """
        return self.amount == 0


def card_for(when=None) -> dict:
    """The rate card in force on ``when`` (today by default).

    Meta publishes the next card before it takes effect -- both live in
    :data:`meta_rates.RATE_CARDS` -- so this picks the newest one whose
    effective date has arrived and the CRM switches over on the day itself
    with no deploy. Before the earliest card's date (only reachable by
    asking about the past) the earliest card is used.
    """
    when = when or timezone.localdate()
    active = [card for card in meta_rates.RATE_CARDS if card["effective"] <= when]
    return active[-1] if active else meta_rates.RATE_CARDS[0]


def rates(when=None) -> dict[str, dict[str, Decimal]]:
    """The active card as ``{market: {category: Decimal}}``, with
    ``MESSAGING_TEMPLATE_RATES`` laid over it.

    Meta's published card is the starting point; the env override exists
    because a rate card is per *account* (a BSP contract, a promotional rate,
    a currency other than USD) and because Meta can change a price between
    releases of this code. Overlay is per market row, so an env that prices
    only Mexico leaves every other market on Meta's numbers.

    A malformed value is logged and ignored -- a typo in an env var must not
    take the app down, and falling back to Meta's published card is the
    conservative outcome (it quotes *a* price rather than pretending the
    send is free).
    """
    # 1. Meta's card, converted from the strings meta_rates stores (Decimal
    #    cannot be a literal, and a float would already have lost 0.0008).
    #    A fresh dict each call, so the overlay below never mutates the
    #    module-level card -- a mutated constant would leak one call's
    #    override, or one test's, into the next.
    card = card_for(when)
    table = {
        market: {category: Decimal(price) for category, price in row.items()}
        for market, row in card["rows"].items()
    }

    # 2. The env override, or "" when unset or None. Read on every call: it
    #    is one small JSON parse, and it means a changed setting
    #    (override_settings in tests included) is honoured at once.
    raw = getattr(settings, "MESSAGING_TEMPLATE_RATES", "") or ""
    if not raw:
        return table

    # 3. Merge row by row, keyed by Meta's market names ("Colombia", "Rest of
    #    Latin America", "North America"...). ``setdefault`` returns the
    #    market's existing row, or inserts an empty one for a market the card
    #    does not know, so an override that prices only Mexico adds nothing
    #    else. Values go through str() before Decimal so a JSON number
    #    (0.0125) is read from its short text form rather than from the
    #    float's binary expansion.
    try:
        override = json.loads(raw)
        for market, row in override.items():
            merged = table.setdefault(str(market), {})
            for category, price in row.items():
                if category not in CATEGORIES:
                    raise ValueError(f"unknown category {category!r}")
                merged[category] = Decimal(str(price))
    # Anything wrong -- invalid JSON, a top-level value or row that is not
    # an object, a price that is not a number, an unknown category -- lands
    # here. The *whole* override is discarded: ``table`` may already be
    # half-merged, so Meta's card is rebuilt clean and returned instead.
    # logger.exception records the traceback at ERROR level.
    except Exception:
        logger.exception("ignoring malformed MESSAGING_TEMPLATE_RATES")
        return {
            market: {category: Decimal(price) for category, price in row.items()}
            for market, row in card["rows"].items()
        }

    return table


def currency() -> str:
    """The currency every amount in this module is expressed in."""
    # ``or "USD"`` also covers MESSAGING_CURRENCY set to an empty string.
    # Meta's shipped card is in USD; set MESSAGING_CURRENCY (and the matching
    # MESSAGING_TEMPLATE_RATES) together if the WABA is billed in another of
    # the 16 currencies Meta publishes.
    return getattr(settings, "MESSAGING_CURRENCY", "USD") or "USD"


def market_for_phone(phone: str) -> str:
    """The Meta market a phone number bills at, or "" if it cannot be placed.

    Meta prices by the recipient's country calling code, so this is the
    resolution that decides the price. Two subtleties, both of them real
    money:

    * **Longest match wins.** "1" (North America), "51" (Peru) and "507"
      (Panama) are all prefixes of some number; matching the longest code
      first is what keeps +507 out of North America.
    * **+1 is not one market.** Dominican Republic, Jamaica and Puerto Rico
      share calling code 1 with the US and Canada but bill at "Rest of Latin
      America" -- 0.0740 against 0.0250 for marketing. A +1 number is
      resolved by its three-digit NANP area code first.

    Known limitation, stated rather than hidden: those three are the only +1
    carve-outs Meta's country-calling-codes table publishes, so every other
    +1 number resolves to North America here. The North American Numbering
    Plan also covers Caribbean and Pacific territories Meta does not list
    (Bahamas, Barbados, Trinidad, Guam...), and Meta's rule for a country it
    does not list is the "Other" row -- dearer than North America. Those
    numbers are therefore under-quoted. Closing the gap needs a NANP
    area-code table Meta does not publish; a full phone-number library
    (libphonenumber) is the honest way to do it if this CRM ever writes to
    the Caribbean.
    """
    digits = "".join(character for character in phone or "" if character.isdigit())
    if not digits:
        return ""

    # NANP first: +1 followed by the area code. Checked before the plain
    # code table so 1-809 never reads as plain "1".
    if digits.startswith("1") and len(digits) >= 4:
        market = meta_rates.MARKET_BY_NANP_AREA.get(digits[1:4])
        if market:
            return market

    # Meta's codes are 1-3 digits; try the longest first.
    for length in (3, 2, 1):
        market = meta_rates.MARKET_BY_CALLING_CODE.get(digits[:length])
        if market:
            return market
    return ""


def market_for(client) -> str:
    """The Meta market that prices a send to this client.

    Only ``.country`` and ``.phone`` are read, via getattr with a default so
    a missing attribute and an empty value behave alike.

    The stored ISO country wins when there is one, because it is what a human
    entered; the phone number answers otherwise. Anything unplaceable falls
    to "Other", which is a real row on Meta's card rather than a guess.
    """
    # 1. The stored country, when it looks like an ISO alpha-2 code -- the
    #    same test Client.flag applies. A country Meta has no row for (say
    #    "EC") maps to its regional bucket, not to a missing row.
    country = (getattr(client, "country", "") or "").upper()
    if len(country) == 2 and country.isalpha():
        market = meta_rates.MARKET_BY_ISO.get(country)
        if market:
            return market

    # 2. Otherwise the phone number decides.
    market = market_for_phone(getattr(client, "phone", "") or "")
    # 3. "Other" is Meta's own catch-all row ("All other countries").
    return market or DEFAULT_MARKET


def rate_for(market: str, category: str, when=None) -> Decimal:
    """List price for one category in one market, per delivered message."""
    # Called by quote() with the market market_for() chose. Re-reads the
    # merged table on each call (see rates()).
    table = rates(when)
    # The market's own row, or the "Other" row. ``or`` (rather than get()'s
    # default) means an *empty* row falls back too, not only a missing one.
    row = table.get(market) or table.get(DEFAULT_MARKET, {})
    price = row.get(category)
    # A row may exist without this category (an override that priced only
    # Mexico's marketing): borrow the category's price from "Other".
    if price is None:
        price = table.get(DEFAULT_MARKET, {}).get(category, Decimal("0"))
    # ``price`` is already a Decimal in every table this module builds; the
    # wrap is a type guarantee, not a conversion.
    return Decimal(price)


def quote(template, client, window_open: bool = False, when=None, service_used: int | None = None) -> Quote:
    """Price one template send to one client.

    An *estimate*, always: Meta charges when a template message is
    **delivered**, not when it is sent, and it bills at the category it has
    assigned the template rather than the one stored here. The authority is
    the ``pricing`` object on the delivery webhook; this is what the CRM can
    know beforehand, which is what the agent needs to see before pressing
    Enviar.

    ``window_open`` is the recipient's 24-hour service window. It matters
    because of a real WhatsApp rule and not as an optimisation: "Utility
    templates sent within an open customer service window are free"
    (developers.facebook.com/documentation/business-messaging/whatsapp/pricing),
    whereas marketing is billed on every delivery. Getting this wrong would
    over-quote every follow-up utility send.

    Called from two places with the same two kinds of object:
    ``core.views._send_form_context`` (to show the price in the dialog
    before anything is sent) and ``messaging.services.send_template`` (to
    bill the send). Both pass a ``core.models.MessageTemplate`` and a
    ``core.models.Client``; ``window_open`` comes from
    ``Conversation.is_within_24h_window`` and is False when the client has
    no open conversation at all. Returns a frozen :class:`Quote`.
    """
    # 1. The billable category. A value outside CATEGORIES (there is none
    #    today -- the model's choices are the same three) is priced as
    #    marketing, which is the dearest column in every market on Meta's
    #    card, so a surprise over-quotes rather than under-quotes.
    category = template.category if template.category in CATEGORIES else "marketing"
    # 2. The recipient's market, then Meta's list price for category+market.
    #    Volume tiers can only discount utility and authentication below
    #    this; the CRM cannot see the portfolio's monthly volume, so the
    #    quote is the undiscounted rate and errs high, never low.
    market = market_for(client)
    unit = rate_for(market, category, when)

    # 3. A utility template inside an open customer service window is billed
    #    as a *service* message, not at the utility rate -- Meta reports it
    #    back as pricing.type "free_customer_service".
    #
    #    Whether that costs anything is read off the card rather than
    #    hardcoded, because it is about to change. On the card effective
    #    2026-07-01 the Service column is "n/a" for every market: in-window
    #    messaging is free. On the card effective 2026-10-01 Meta prices
    #    Service in all 47 markets -- at exactly each market's utility rate --
    #    so the same send starts costing money that day. Asking the card
    #    keeps the quote right on both sides of the switch with no deploy and
    #    no date literal to forget.
    #
    #    ``amount`` is what this send costs; ``unit_amount`` keeps the
    #    category's own list price either way, so the dialog can still show
    #    what a plain utility send would have cost. The reason is
    #    user-facing Spanish: send_form.html prints it verbatim.
    #
    #    Not modelled: the free entry point window (72 hours in which every
    #    category is free, after replying to a Click-to-WhatsApp ad). The CRM
    #    does not record the inbound `referral` object that opens one, so a
    #    send inside such a window is quoted as billable and Meta reports it
    #    free. Erring that way keeps the estimate above the invoice.
    free_reason = ""
    amount = unit
    billed_as_service = False
    if window_open and category == "utility":
        billed_as_service = True
        amount = rate_for(market, "service", when)
        if amount == 0:
            # The card in force prices Service as "n/a": in-window messaging
            # is simply free, and the allowance below has nothing to meter.
            free_reason = (
                "Ventana de 24 horas abierta: las plantillas de servicio no se cobran."
            )
        else:
            # Service is priced, so the monthly free allowance decides.
            # ``service_used`` is how many service-rate sends this month has
            # already had; callers pass it (see service_used_this_month) so a
            # dialog listing ten plantillas counts once rather than ten
            # times. None means "unknown", and an unknown allowance is
            # treated as spent -- quoting a price nobody is charged is a
            # smaller error than promising free and billing for it.
            allowance = service_allowance()
            used = service_used if service_used is not None else allowance
            if used < allowance:
                amount = Decimal("0")
                free_reason = (
                    "Ventana de 24 horas abierta: entra en los "
                    f"{allowance} mensajes de servicio gratis del mes "
                    f"({used} usados)."
                )
            else:
                free_reason = (
                    f"Ventana de 24 horas abierta: agotados los {allowance} "
                    "mensajes de servicio gratis del mes, se cobra la tarifa "
                    "de servicio."
                )

    # 4. Freeze the answer. Nothing downstream recomputes it: send_template
    #    copies these fields onto the Message row as they are.
    return Quote(
        category=category,
        market=market,
        currency=currency(),
        unit_amount=unit,
        amount=amount,
        free_reason=free_reason,
        billed_as_service=billed_as_service,
    )


# --- Spend ------------------------------------------------------------------
#
# Everything above this line is arithmetic over settings and two model
# instances; the functions below are the only ones that query the database.


def spent_between(start, end=None) -> Decimal:
    """Total billed for template sends in ``[start, end)``.

    Reads the frozen ``billed_amount`` on the Message rows rather than
    re-pricing them, so a rate change never rewrites what the past cost.
    Failed sends carry zero (see ``services.send_template``) -- WhatsApp does
    not bill a message it never delivered.
    """
    # Importing the ORM here rather than at module scope means Django model
    # classes (which can only be imported once the app registry is ready)
    # are not needed just to import this module's pricing arithmetic.
    # ``Sum`` is the SQL SUM() aggregate.
    from django.db.models import Sum

    from .models import Message

    # ``billed_amount__isnull=False`` is SQL ``IS NOT NULL``: only rows that
    # were billed (inbound and free-form messages carry NULL). ``__gte`` is
    # ``>=``. This is exactly the shape the partial index
    # ``message_billed_timestamp_idx`` in Message.Meta is built for. Nothing
    # has hit the database yet -- a queryset is a description of a query.
    queryset = Message.objects.filter(billed_amount__isnull=False, timestamp__gte=start)
    # ``__lt`` is ``<``: the range is half-open, so one month's end is the
    # next month's start with no overlap.
    if end is not None:
        queryset = queryset.filter(timestamp__lt=end)
    # One ``SELECT SUM(billed_amount) ...`` query. aggregate() returns a dict,
    # here {"total": ...}; SUM over no rows is NULL, which Django hands back
    # as None, hence the ``or Decimal("0")``. Because billed_amount is a
    # DecimalField the total comes back as a Decimal, never a float. Failed
    # sends hold 0 rather than NULL, so they are counted and add nothing.
    return queryset.aggregate(total=Sum("billed_amount"))["total"] or Decimal("0")


def month_start(now=None):
    """Midnight on the 1st of the current month, in :data:`BILLING_TZ` --
    the boundary WhatsApp bills on, and what the UI means by "este mes"."""
    # ``now`` is a parameter so callers and tests can pin the date; it
    # defaults to the present. The conversion is to BILLING_TZ, not to
    # settings.TIME_ZONE: this project runs on UTC, so localtime() here would
    # put the 5 hours after 19:00 Bogotá on the month's last day into the
    # *next* month's spend -- and the ceiling would then be enforced against
    # a month the agent who sent it does not recognise.
    now = (now or timezone.now()).astimezone(BILLING_TZ)
    # replace() keeps year, month and tzinfo and zeroes everything else: an
    # aware datetime at local midnight on day 1, comparable with
    # Message.timestamp.
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_to_date(now=None) -> Decimal:
    """What template sends have cost since the 1st of this month."""
    # No ``end``: everything from the 1st onward counts. Called by
    # budget_state() (for display) and would_exceed_budget() (for the guard).
    return spent_between(month_start(now))


def service_allowance() -> int:
    """How many service messages a phone number gets free each month.

    From 2026-10-01 Meta charges for service messages -- everything sent
    inside an open 24-hour window, template or not -- and gives each business
    phone number a monthly allowance before the meter starts. The published
    figure is 1,000, and it does not roll over.

    Configurable, and deliberately so: the allowance is the least
    well-corroborated part of the October change (Meta's own pricing page is
    the single source; several BSP write-ups of the same change omit it
    entirely). Set MESSAGING_SERVICE_FREE_ALLOWANCE to 0 to bill every
    service message from the first, which is what an account that turns out
    not to have the allowance should do.

    "Per phone number" matches "per account" here: the CRM sends from one
    number (META_PHONE_NUMBER_ID). A second line would need the count split
    per number, which the Message table cannot do -- it does not record which
    number a message left from.
    """
    raw = getattr(settings, "MESSAGING_SERVICE_FREE_ALLOWANCE", "1000")
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        logger.warning(
            "ignoring malformed MESSAGING_SERVICE_FREE_ALLOWANCE %r", raw
        )
        return 1000
    return max(value, 0)


def service_used_this_month(now=None) -> int:
    """Service-rate sends already made this calendar month.

    Counted from ``Message.billed_as_service``, which ``send_template``
    stamps at send time and the delivery receipt never rewrites. Failed sends
    are excluded: Meta bills on delivery, so a message that never left does
    not eat the allowance.

    One query, and callers are meant to make it once and hand the number to
    :func:`quote` -- the send dialog prices every plantilla on the list, and
    counting per plantilla would be one query per row.
    """
    from .models import Message
    from .providers.types import MessageStatus

    return (
        Message.objects.filter(
            billed_as_service=True, timestamp__gte=month_start(now)
        )
        .exclude(status=MessageStatus.FAILED.value)
        .count()
    )


def budget() -> Decimal:
    """The optional monthly ceiling, ``0`` meaning "no limit".

    Unset by default: an MVP that silently refused to send because of a
    number nobody chose would be worse than one that spends. Set
    MESSAGING_MONTHLY_BUDGET to turn it on.
    """
    # The setting is a string from the environment; ``or "0"`` also covers
    # an empty MESSAGING_MONTHLY_BUDGET=.
    raw = getattr(settings, "MESSAGING_MONTHLY_BUDGET", "0") or "0"
    # str() first, so a value set as a number (a test's override_settings,
    # or a settings file that used one) parses the same as the env string.
    # Decimal("0.02") from the string is exact.
    try:
        value = Decimal(str(raw))
    # Decimal() raises decimal.InvalidOperation for text that is not a
    # number; a bad value means "no limit" rather than "no sends".
    except Exception:
        logger.warning("ignoring malformed MESSAGING_MONTHLY_BUDGET %r", raw)
        return Decimal("0")
    # Zero and negatives both collapse to 0, the "no limit" value the callers
    # test with ``if not cap``.
    return value if value > 0 else Decimal("0")


def budget_state(now=None) -> dict:
    """Everything the UI needs to talk about money in one dict: spent this
    month, the ceiling (or None), and what is left under it."""
    # Built for the templates: send_form.html and send_sent.html read it as
    # ``budget.spent`` / ``budget.budget`` (Django's dotted lookup works on
    # dict keys). core.views calls it when rendering the dialog and again
    # after a successful send, so the total shown includes that send.
    spent = month_to_date(now)
    cap = budget()
    # ``cap or None`` turns the 0 meaning "no limit" into None, so the
    # template can write ``{% if budget.budget %}``. ``remaining`` is not
    # clamped: it goes negative when the month is already over the cap.
    return {
        "currency": currency(),
        "spent": spent,
        "budget": cap or None,
        "remaining": (cap - spent) if cap else None,
    }


def would_exceed_budget(amount: Decimal, now=None) -> bool:
    """Whether billing ``amount`` now would push this month past the cap.

    Checked before the provider call in ``services.send_template``; with no
    cap configured this is always False.
    """
    # 1. No cap configured: answer without touching the database.
    cap = budget()
    if not cap:
        return False
    # 2. Strict ``>``: a send that lands exactly on the cap is still allowed.
    #    Plain read-then-compare with no lock, so two sends arriving at the
    #    same instant could both pass; the Message row each one then writes
    #    makes the next check see both.
    return month_to_date(now) + amount > cap


# --- Meta's own ledger -------------------------------------------------------
#
# Everything above is the CRM's arithmetic. These read what *Meta* says the
# account was charged, which is the only figure that can be compared with an
# invoice -- and even then Meta calls it approximate.


#: Meta spells a category three ways across its own surfaces: hyphenated and
#: lower case in the status webhook (``authentication-international``),
#: upper case with an underscore on the analytics endpoint
#: (``AUTHENTICATION_INTERNATIONAL``). One canonical form here, so a webhook
#: verdict and an analytics row about the same message compare equal.
def canonical_category(value: str) -> str:
    """Meta's category in one spelling: lower case, hyphen-separated."""
    return (value or "").strip().lower().replace("_", "-")


def crm_spend_by_category(start, end=None) -> dict:
    """What the CRM's own ledger says it spent in a window, per category.

    The counterpart to :func:`meta_spend_by_category`: same window, same
    shape, so the two can be subtracted. Amounts are the ``billed_amount``
    frozen on each Message and then corrected by Meta's delivery receipt
    (``services._apply_pricing``), so this is already Meta-informed per
    message -- the sweep exists to catch what per-message reconciliation
    cannot see, such as a message the webhook never arrived for.
    """
    from django.db.models import Sum

    from .models import Message

    queryset = Message.objects.filter(
        billed_amount__isnull=False, timestamp__gte=start
    )
    if end is not None:
        queryset = queryset.filter(timestamp__lt=end)

    totals = {}
    rows = queryset.values("billed_category").annotate(total=Sum("billed_amount"))
    for row in rows:
        totals[canonical_category(row["billed_category"])] = row["total"] or Decimal("0")
    return totals


def meta_spend_by_category(data_points) -> dict:
    """Meta's analytics data points folded into ``{category: cost}``.

    ``cost`` is a float in Meta's payload; it becomes a Decimal via ``str``
    so the value is read from its short text form rather than the float's
    binary expansion. A point with no ``cost`` key is counted as unknown, not
    as zero: Meta omits cost entirely for an account billed through a
    solution partner's credit line, and reporting that as "spent nothing"
    would be a lie the size of the whole invoice.

    Returns ``{category: Decimal}`` plus two bookkeeping keys under
    ``"_meta"``: how many points carried no cost, and the total volume.
    """
    totals, missing, volume = {}, 0, 0
    for point in data_points or []:
        volume += int(point.get("volume") or 0)
        if "cost" not in point or point.get("cost") is None:
            missing += 1
            continue
        category = canonical_category(point.get("pricing_category"))
        totals[category] = totals.get(category, Decimal("0")) + Decimal(
            str(point["cost"])
        )
    totals["_meta"] = {"points_without_cost": missing, "volume": volume}
    return totals
