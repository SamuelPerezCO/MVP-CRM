"""What a template send costs, and what has been spent so far.

The 24-hour window rule has a price tag behind it. A free-form reply inside
the window is free; reaching someone *outside* it -- a client who has never
written, which is exactly the "clientes nuevos" case -- means sending an
approved template, and WhatsApp bills every one of those per message, priced
by the template's **category** and the recipient's **country**.

So this module answers two questions, and nothing else touches them:

* :func:`quote` -- what will this particular send cost, before it happens.
  The UI shows it next to the Enviar button; ``services.send_template``
  freezes the answer onto the Message row (rates change, history must not).
* :func:`month_to_date` / :func:`budget` -- what has been spent this month,
  and the optional ceiling that stops the sending once it is reached.

Rates are *configuration*, not code. :data:`DEFAULT_RATES` below is a
starting point in the shape Meta's price list has, not an authoritative copy
of it: Meta publishes per-country rates that change over time, and each
account sees its own. Put the real ones in ``MESSAGING_TEMPLATE_RATES``
(JSON, see .env.example) before quoting money at anyone.

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
  pricing half is arithmetic over settings plus the ``category`` of a
  MessageTemplate and the ``country``/``phone`` of a Client.
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

# Named after the module ("messaging.pricing"), so log filtering can single
# out the two "ignoring malformed ..." messages this file emits.
logger = logging.getLogger(__name__)

# (Comments starting with ``#:`` document the constant right below them; it
# is the Sphinx convention this file uses for module-level values.)
#
# CATEGORIES is used two ways: quote() prices anything outside this tuple
# as "marketing", and rates() rejects an override naming a category that
# is not in it.
#: The billable categories -- the same keys as
#: ``core.models.MessageTemplate.CATEGORY_CHOICES``, because a template's
#: category *is* what it is billed as.
CATEGORIES = ("marketing", "utility", "authentication")

# Every lookup can end here: rate_for() reads this row when a country has
# no row of its own, or has one that lacks the category asked for.
#: Country key for "no specific rate": the fallback row of the price list.
DEFAULT_COUNTRY = ""

# Prices are built with Decimal("0.0125") -- from a *string* -- on purpose:
# Decimal(0.0125) would copy the float literal's binary rounding error into
# the price. rates() copies this table before overlaying the env override,
# so the constant itself is never mutated.
#: Placeholder price list, USD per template message. Structure is the point:
#: country (ISO 3166-1 alpha-2) -> category -> price, plus a "" fallback row
#: for every country without one of its own. Colombia is spelled out because
#: it is this CRM's home market (the same +57 assumption the contact upsert
#: makes); every other country falls back until someone adds it.
#:
#: These numbers are illustrative. Replace them with your account's real
#: rates via MESSAGING_TEMPLATE_RATES rather than editing this file, so a
#: price change is a redeploy variable and not a code change.
DEFAULT_RATES = {
    "CO": {
        "marketing": Decimal("0.0125"),
        "utility": Decimal("0.0022"),
        "authentication": Decimal("0.0077"),
    },
    DEFAULT_COUNTRY: {
        "marketing": Decimal("0.0500"),
        "utility": Decimal("0.0100"),
        "authentication": Decimal("0.0300"),
    },
}

# Module-private (leading underscore); read only by country_for().
#: Phone prefixes we can turn into a country when the Client row has none.
#: Same stance as ``services._upsert_contact``: recognise +57, guess nothing
#: else -- a wrong country here would quote the wrong price.
_PREFIX_COUNTRIES = {"+57": "CO"}


# ``@dataclass(frozen=True)`` writes __init__/__repr__/__eq__ from the field
# list in the class body and makes instances read-only: assigning to a
# field afterwards raises FrozenInstanceError, so a quote handed to a
# template or copied onto a Message row is exactly what quote() computed.
@dataclass(frozen=True)
class Quote:
    """What one template send will be billed.

    ``unit_amount`` is the list price for this category+country; ``amount``
    is what this send actually costs, which is zero when a rule makes it
    free (``free_reason`` says which). Both travel to the UI, so the dialog
    can show "gratis" *and* what it would otherwise have cost.
    """

    # Who reads what: send_form.html prints ``currency``, ``amount``,
    # ``unit_amount``, ``country`` and ``free_reason``; services.send_template
    # copies ``category``/``amount``/``currency`` onto the Message row as
    # billed_category/billed_amount/billed_currency.
    category: str
    country: str
    currency: str
    unit_amount: Decimal
    amount: Decimal
    free_reason: str = ""

    @property
    def is_free(self) -> bool:
        """True when nothing will be charged for this send.

        ``Decimal("0") == 0`` holds, so no conversion is needed. Templates
        read it as ``quote.is_free``: Django's dotted lookup resolves
        attributes, so a property needs no parentheses.
        """
        return self.amount == 0


def rates() -> dict[str, dict[str, Decimal]]:
    """The active price list: :data:`DEFAULT_RATES` overlaid with whatever
    ``MESSAGING_TEMPLATE_RATES`` holds.

    Overlay per country row, so an env that prices only Mexico keeps every
    other country's defaults instead of blanking the list. A malformed value
    is logged and ignored -- a typo in an env var must not take the app down,
    and falling back to the shipped list is the conservative outcome (it
    quotes *a* price rather than pretending the send is free).
    """
    # 1. Start from a copy -- new outer dict, new inner dicts -- so the
    #    overlay below never mutates DEFAULT_RATES itself. A mutated constant
    #    would leak one call's override (or one test's) into the next.
    table = {country: dict(row) for country, row in DEFAULT_RATES.items()}

    # 2. The env override, or "" when unset or None. Read on every call: it
    #    is one small JSON parse, and it means a changed setting
    #    (override_settings in tests included) is honoured at once.
    raw = getattr(settings, "MESSAGING_TEMPLATE_RATES", "") or ""
    if not raw:
        return table

    # 3. Merge row by row. ``setdefault`` returns the country's existing row,
    #    or inserts an empty one for a country the defaults do not know, so
    #    an override that prices only MX adds MX and leaves CO and "" alone.
    #    An empty-string key becomes the fallback row. Values go through
    #    str() before Decimal so a JSON number (0.0125) is read from its
    #    short text form rather than from the float's binary expansion.
    try:
        override = json.loads(raw)
        for country, row in override.items():
            country = str(country).upper() if country else DEFAULT_COUNTRY
            merged = table.setdefault(country, {})
            for category, price in row.items():
                if category not in CATEGORIES:
                    raise ValueError(f"unknown category {category!r}")
                merged[category] = Decimal(str(price))
    # Anything wrong -- invalid JSON, a top-level value or row that is not
    # an object, a price that is not a number, an unknown category -- lands
    # here. The *whole* override is discarded: ``table`` may already be
    # half-merged, so a fresh copy of the defaults is returned instead of
    # it. logger.exception records the traceback at ERROR level.
    except Exception:
        logger.exception("ignoring malformed MESSAGING_TEMPLATE_RATES")
        return {country: dict(row) for country, row in DEFAULT_RATES.items()}

    return table


def currency() -> str:
    """The currency every amount in this module is expressed in."""
    # ``or "USD"`` also covers MESSAGING_CURRENCY set to an empty string.
    # Called by quote() (it becomes Quote.currency) and by budget_state().
    return getattr(settings, "MESSAGING_CURRENCY", "USD") or "USD"


def country_for(client) -> str:
    """The price-list country for a client: their stored country, else what
    the phone prefix says, else "" (the fallback row)."""
    # Called by quote(). ``client`` is a core.models.Client in practice, but
    # only ``.country`` and ``.phone`` are read, via getattr with a default
    # and ``or ""`` so a missing attribute and an empty value behave alike.
    #
    # 1. The stored country wins when it looks like an ISO alpha-2 code (two
    #    letters) -- the same test Client.flag applies.
    country = (getattr(client, "country", "") or "").upper()
    if len(country) == 2 and country.isalpha():
        return country

    # 2. Otherwise read the country off the phone prefix. Only +57 is known,
    #    so e.g. a Mexican number with a blank country field ends at step 3.
    phone = getattr(client, "phone", "") or ""
    for prefix, code in _PREFIX_COUNTRIES.items():
        if phone.startswith(prefix):
            return code
    # 3. Unknown: "" selects the fallback row of the price list. Note that
    #    this function reports the client's country, not the row that will
    #    price it -- a client stored as "MX" returns "MX" even though
    #    rate_for() then falls back to "" for lack of an MX row.
    return DEFAULT_COUNTRY


def rate_for(country: str, category: str) -> Decimal:
    """List price for one category in one country, falling back to the ""
    row for countries with no rates of their own."""
    # Called by quote() with the country country_for() chose. Re-reads the
    # merged table on each call (see rates()).
    table = rates()
    # The country's own row, or the "" row. ``or`` (rather than get()'s
    # default) means an *empty* row falls back too, not only a missing one.
    row = table.get((country or "").upper()) or table.get(DEFAULT_COUNTRY, {})
    price = row.get(category)
    # A country row may exist without this category (an override that priced
    # only MX marketing): borrow the category's price from the fallback row.
    # Decimal("0") is the last resort, reachable only for a category the
    # shipped fallback row does not list -- which quote() never asks for.
    if price is None:
        price = table.get(DEFAULT_COUNTRY, {}).get(category, Decimal("0"))
    # ``price`` is already a Decimal in every table this module builds; the
    # wrap is a type guarantee, not a conversion.
    return Decimal(price)


def quote(template, client, window_open: bool = False) -> Quote:
    """Price one template send to one client.

    ``window_open`` is the recipient's 24-hour service window. It matters
    because of a real WhatsApp rule and not as an optimisation: a *utility*
    template sent while that window is open rides the free service
    conversation, whereas marketing and authentication are billed either
    way. Getting this wrong would over-quote every follow-up utility send.

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
    #    marketing, the dearest of the three in the shipped list, so a
    #    surprise over-quotes rather than under-quotes.
    category = template.category if template.category in CATEGORIES else "marketing"
    # 2. The recipient's country, then the list price for category+country.
    country = country_for(client)
    unit = rate_for(country, category)

    # 3. The one rule that makes a send free: a utility template inside an
    #    open window. ``amount`` starts equal to the list price and is
    #    zeroed only then; ``unit_amount`` keeps the list price either way
    #    so the dialog can still show it. The reason is user-facing Spanish
    #    because send_form.html prints it verbatim.
    free_reason = ""
    amount = unit
    if window_open and category == "utility":
        amount = Decimal("0")
        free_reason = "Ventana de 24 horas abierta: las plantillas de servicio no se cobran."

    # 4. Freeze the answer. Nothing downstream recomputes it: send_template
    #    copies these fields onto the Message row as they are.
    return Quote(
        category=category,
        country=country,
        currency=currency(),
        unit_amount=unit,
        amount=amount,
        free_reason=free_reason,
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
    """Midnight on the 1st of the current month, in the active timezone --
    the boundary WhatsApp bills on, and what the UI means by "este mes"."""
    # ``now`` is a parameter so callers and tests can pin the date; it
    # defaults to the present. localtime() converts the aware UTC instant to
    # the active time zone (settings.TIME_ZONE, 'UTC' in this project,
    # unless timezone.activate() set another), so "the 1st" is the local 1st.
    now = timezone.localtime(now or timezone.now())
    # replace() keeps year, month and tzinfo and zeroes everything else: an
    # aware datetime at local midnight on day 1, comparable with
    # Message.timestamp.
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_to_date(now=None) -> Decimal:
    """What template sends have cost since the 1st of this month."""
    # No ``end``: everything from the 1st onward counts. Called by
    # budget_state() (for display) and would_exceed_budget() (for the guard).
    return spent_between(month_start(now))


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
