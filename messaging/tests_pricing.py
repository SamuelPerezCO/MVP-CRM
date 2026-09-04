"""Tests for messaging.pricing: what a template send costs and what the
month has cost so far.

The prices themselves are configuration (and the shipped table is a
placeholder), so nothing here asserts a particular tariff. What is pinned is
the behaviour around it: the country and category a send is priced by, the
rule that makes some sends free, the env override, and the fact that a
recorded cost never changes afterwards.

How to read this file if Django's test tooling is new to you:

* Run it with ``python manage.py test messaging.tests_pricing``. The runner
  finds every ``TestCase`` subclass here and calls each of its ``test_*``
  methods; a method that raises (an ``assert*`` failing raises) is a failure.
* ``django.test.TestCase`` gives each test method a *separate* database
  transaction that is rolled back when the method ends. So the rows a test
  creates are gone by the next one, ``setUp`` really does start from an
  empty table, and nothing here needs to clean up after itself.
* ``override_settings`` swaps values into ``django.conf.settings`` and puts
  the old ones back afterwards. It works as a class decorator (every test in
  the class sees the value), a method decorator (only that test) or a
  ``with`` block (only that block). It is the whole reason this file can pin
  prices: :mod:`messaging.pricing` re-reads settings on every call and
  caches nothing, so a swapped-in value takes effect immediately.
* The two halves of the module are tested apart: ``QuoteTests`` and
  ``RateTableTests`` touch no database at all (pricing is arithmetic over
  settings plus two model instances), while ``SpendTests`` is about the
  ``Message`` rows a month's sends leave behind.
"""

from __future__ import annotations

# json: the rate override is configured as a JSON string, so the tests build
# one the same way .env would. timedelta: for backdating a message into last
# month. Decimal: money is compared as Decimal throughout -- 0.0125 has no
# exact float form, so comparing against 0.0125 as a float could fail on the
# last bits even when the code is right.
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Client, MessageTemplate

# The module under test, plus the two models whose rows the spend half sums.
from . import pricing
from .models import Conversation, Message

# A price list in exactly the shape MESSAGING_TEMPLATE_RATES takes: a JSON
# object of country -> category -> price. Prices are strings on purpose, the
# same reason pricing.DEFAULT_RATES uses Decimal("..."): a JSON number would
# be parsed as a float first. The "" row is the fallback for every country
# without one of its own, which is what makes the MX test below meaningful.
RATES = json.dumps(
    {
        "CO": {"marketing": "0.0125", "utility": "0.0022", "authentication": "0.0077"},
        "": {"marketing": "0.0500", "utility": "0.0100", "authentication": "0.0300"},
    }
)


# Insert one CRM client and return it. ``objects.create()`` builds the
# instance, INSERTs it and hands it back with its primary key filled in.
# Pricing only ever reads ``country`` and ``phone`` off it (see
# pricing.country_for), so those are the two parameters worth naming here;
# ``**extra`` passes anything else straight to the model.
def client(phone="+573000000001", country="CO", **extra):
    return Client.objects.create(first_name="Camila", phone=phone, country=country, **extra)


# Insert one plantilla. ``category`` is the only field pricing looks at, so
# it leads; ``extra.pop(name, default)`` is the pattern that lets a caller
# override a field while keeping a default for the rest. Note the model's
# unique constraint on (name, language): two plantillas in the same test need
# different names.
def template(category="marketing", **extra):
    return MessageTemplate.objects.create(
        name=extra.pop("name", "saludo_inicial"),
        body=extra.pop("body", "Hola {{1}}"),
        body_sample_values=extra.pop("body_sample_values", ["Camila"]),
        category=category,
        **extra,
    )


# pricing.quote: what one send costs, the function the send dialog calls to
# show a price and services.send_template calls again to bill it.
#
# Pinned for the whole class: every test below prices against RATES above and
# in USD, whatever the developer's own .env holds.
@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_CURRENCY="USD")
class QuoteTests(TestCase):
    # The ordinary case: the price comes from the client's country row and
    # the plantilla's category, and the Quote reports both back.
    def test_prices_by_category_and_country(self):
        quote = pricing.quote(template("marketing"), client())
        self.assertEqual(quote.amount, Decimal("0.0125"))
        self.assertEqual(quote.country, "CO")
        self.assertEqual(quote.category, "marketing")
        self.assertEqual(quote.currency, "USD")

    # RATES has no MX row, so rate_for falls back to the "" row (0.0500).
    # Only the amount is asserted: the quote still reports country "MX" --
    # country_for names the client's country, not the row that priced it.
    def test_a_country_without_its_own_rates_falls_back(self):
        quote = pricing.quote(template("marketing"), client(phone="+521", country="MX"))
        self.assertEqual(quote.amount, Decimal("0.0500"))

    # With the country field blank, country_for reads the phone prefix
    # instead: +57 is the one prefix it knows, so this prices as Colombia.
    def test_the_phone_prefix_names_the_country_when_the_field_is_empty(self):
        quote = pricing.quote(template("marketing"), client(country=""))
        self.assertEqual(quote.country, "CO")
        self.assertEqual(quote.amount, Decimal("0.0125"))

    def test_utility_inside_the_open_window_is_free(self):
        # A real WhatsApp rule, and the reason window state reaches pricing:
        # a utility template rides the open service conversation.
        # In production ``window_open`` comes from
        # Conversation.is_within_24h_window; here it is passed by hand, which
        # is why this test needs no conversation at all.
        quote = pricing.quote(template("utility"), client(), window_open=True)
        self.assertTrue(quote.is_free)
        self.assertEqual(quote.amount, Decimal("0"))
        # The list price still travels, so the UI can say what it saved.
        self.assertEqual(quote.unit_amount, Decimal("0.0022"))
        self.assertTrue(quote.free_reason)

    # The same plantilla outside the window: the free rule is about the
    # window, not about the category on its own.
    def test_utility_outside_the_window_is_charged(self):
        quote = pricing.quote(template("utility"), client(), window_open=False)
        self.assertFalse(quote.is_free)
        self.assertEqual(quote.amount, Decimal("0.0022"))

    # And the other half of that rule: only utility is freed by an open
    # window, so a marketing send costs the same either way.
    def test_marketing_is_charged_even_inside_the_window(self):
        quote = pricing.quote(template("marketing"), client(), window_open=True)
        self.assertEqual(quote.amount, Decimal("0.0125"))


# How the price list itself is assembled, one level below quote(): the
# shipped DEFAULT_RATES with the MESSAGING_TEMPLATE_RATES override laid over
# it, and what happens when that override is wrong.
class RateTableTests(TestCase):
    # rates() overlays the override onto DEFAULT_RATES row by row rather than
    # replacing the table, which is what this pins: pricing one CO category
    # must not blank out the others or the other countries. override_settings
    # is used as a ``with`` block here because the value differs per test, so
    # the class-level decorator pattern would not fit.
    def test_the_env_overrides_only_the_rows_it_names(self):
        override = json.dumps({"CO": {"marketing": "0.99"}})
        with override_settings(MESSAGING_TEMPLATE_RATES=override):
            self.assertEqual(pricing.rate_for("CO", "marketing"), Decimal("0.99"))
            # Untouched category and untouched country keep their defaults.
            # Compared against pricing.DEFAULT_RATES itself, so the assertion
            # keeps holding if someone edits the shipped placeholder numbers.
            self.assertEqual(
                pricing.rate_for("CO", "utility"),
                pricing.DEFAULT_RATES["CO"]["utility"],
            )
            self.assertEqual(
                pricing.rate_for("XX", "marketing"),
                pricing.DEFAULT_RATES[""]["marketing"],
            )

    def test_a_malformed_override_falls_back_to_the_shipped_table(self):
        # A typo in an env var must not take the app down -- and must not
        # quietly price everything at zero either.
        # Two ways to be malformed: text that is not JSON at all, and valid
        # JSON naming a category outside pricing.CATEGORIES. subTest reports
        # each iteration separately, so a failure on one still shows the
        # other and names which value caused it.
        for bad in ("{not json", json.dumps({"CO": {"promocional": "0.01"}})):
            with self.subTest(bad=bad), override_settings(MESSAGING_TEMPLATE_RATES=bad):
                self.assertEqual(
                    pricing.rate_for("CO", "marketing"),
                    pricing.DEFAULT_RATES["CO"]["marketing"],
                )


# The other half of the module: what a month of sends has added up to
# (month_to_date over the billed Message rows) and the optional ceiling that
# stops the next one (budget / would_exceed_budget / budget_state).
#
# Only the rates are pinned here: these tests are about summing amounts
# already stored on rows, so the currency setting never comes into it.
@override_settings(MESSAGING_TEMPLATE_RATES=RATES)
class SpendTests(TestCase):
    # setUp runs before each test method, inside that method's own
    # transaction, so every test gets a fresh conversation and an empty
    # Message table.
    def setUp(self):
        self.conversation = Conversation.objects.create(contact=client())

    # Write one billed outbound message, the shape services.send_template
    # leaves behind. ``when`` backdates it: Message.timestamp defaults to
    # timezone.now at creation, and ``Message.objects.filter(pk=...).update()``
    # writes the column with a single SQL UPDATE that skips save() entirely.
    # That also means the returned instance still holds the original
    # timestamp -- fine here, since the tests read the total, not the row.
    def bill(self, amount, when=None):
        message = Message.objects.create(
            conversation=self.conversation,
            direction=Message.OUTBOUND,
            body="Hola",
            billed_amount=Decimal(amount),
            billed_currency="USD",
            billed_category="marketing",
        )
        if when is not None:
            Message.objects.filter(pk=message.pk).update(timestamp=when)
        return message

    # The plain case: SUM over this month's billed rows. Decimal arithmetic,
    # so 0.0125 + 0.0125 is exactly 0.025 with no rounding to allow for.
    def test_month_to_date_adds_up_the_billed_messages(self):
        self.bill("0.0125")
        self.bill("0.0125")
        self.assertEqual(pricing.month_to_date(), Decimal("0.025"))

    def test_unbilled_messages_are_not_counted(self):
        # Free-form replies and everything inbound carry NULL, not zero.
        # This row is created without any billed_* value, so spent_between's
        # billed_amount__isnull=False filter skips it; SUM over no rows is
        # NULL, which the module turns into Decimal("0").
        Message.objects.create(
            conversation=self.conversation, direction=Message.OUTBOUND, body="Claro"
        )
        self.assertEqual(pricing.month_to_date(), Decimal("0"))

    # month_to_date only counts from month_start() onward, so a row dated one
    # day before the 1st is outside the range however large it is.
    def test_last_month_is_not_counted(self):
        self.bill("5.00", when=pricing.month_start() - timedelta(days=1))
        self.bill("0.0125")
        self.assertEqual(pricing.month_to_date(), Decimal("0.0125"))

    # Only this test runs with a ceiling, hence the method-level decorator.
    # 0.0125 is already spent against a cap of 0.02: another 0.0075 lands
    # exactly on the cap and is allowed (the check is a strict ``>``), while
    # 0.01 would reach 0.0225 and is refused.
    @override_settings(MESSAGING_MONTHLY_BUDGET="0.02")
    def test_the_budget_stops_the_send_that_would_cross_it(self):
        self.bill("0.0125")
        self.assertFalse(pricing.would_exceed_budget(Decimal("0.0075")))
        self.assertTrue(pricing.would_exceed_budget(Decimal("0.01")))

    # The default (MESSAGING_MONTHLY_BUDGET "0") means "no limit": budget()
    # returns 0, would_exceed_budget answers False without even querying the
    # spend, and budget_state reports the ceiling as None so the UI can leave
    # it out.
    def test_without_a_budget_nothing_is_ever_exceeded(self):
        self.bill("999")
        self.assertFalse(pricing.would_exceed_budget(Decimal("999")))
        self.assertIsNone(pricing.budget_state()["budget"])

    # A ceiling that cannot be parsed is treated as no ceiling rather than as
    # zero -- zero would read as "no limit" too, but only after Decimal()
    # raised; this pins that the failure is caught and logged instead.
    @override_settings(MESSAGING_MONTHLY_BUDGET="no-soy-un-numero")
    def test_a_malformed_budget_is_ignored_rather_than_blocking_sends(self):
        self.assertEqual(pricing.budget(), Decimal("0"))
        self.assertFalse(pricing.would_exceed_budget(Decimal("1")))
