"""Tests for sending one of the CRM's own plantillas to a client.

This is the path that reaches a *new* client -- nobody has written to us, so
the 24-hour window was never open and only a template gets through. Two
things have to hold every time: the message really goes out through the
provider with the variables filled in, and what it cost is recorded before
anyone can forget that these sends are billed.

These are service-layer tests: they call ``messaging.services`` directly and
never go through a URL. The screen half -- the dialog, the buttons that open
it, the error lines -- is pinned in ``core.tests_plantilla_envio``.

Django test mechanics used throughout, in case they are new:

* ``django.test.TestCase`` wraps each test method in its own database
  transaction and rolls it back afterwards, so rows never leak from one test
  into the next and ``setUp`` always starts from empty tables.
* ``override_settings`` swaps values into ``django.conf.settings`` for the
  class, method or ``with`` block it decorates, then restores them.
  ``messaging.pricing`` re-reads settings on every call, so the prices below
  take effect with nothing to invalidate.
* No real WhatsApp traffic happens. ``config/settings.py`` forces
  ``MESSAGING_PROVIDER = 'fake'`` while the test runner is active, so
  ``services.get_provider()`` returns the in-process FakeProvider, which
  makes up a message id instead of calling out. Where a test needs to see
  *what* the provider was asked, or to make it fail, it swaps in a ``Mock``
  with ``patch.object`` instead.
"""

from __future__ import annotations

# json: to build a MESSAGING_TEMPLATE_RATES value the way .env would.
# Decimal: money is compared as Decimal, never float -- 0.0125 has no exact
# binary form. Mock/patch: to stand in for the provider (see the module
# docstring).
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Client, MessageTemplate

# The module under test, the two models it writes, and the status enum whose
# ``.value`` is what actually lands in Message.status.
from . import services
from .models import Conversation, Message
from .providers.types import MessageStatus

# Only a CO row here: every client in this file is Colombian, so no fallback
# row is needed. Prices are strings for the same reason pricing.DEFAULT_RATES
# uses Decimal("..."): a JSON number would be read as a float first.
RATES = json.dumps(
    {"CO": {"marketing": "0.0125", "utility": "0.0022", "authentication": "0.0077"}}
)


# Insert one CRM client. ``extra.pop(key, default)`` is the pattern that lets
# a caller override any of these while the rest keep their defaults; whatever
# is left in ``extra`` goes straight to the model. The +57 number and the CO
# country are what make the RATES table above apply.
def make_client(**extra):
    return Client.objects.create(
        first_name=extra.pop("first_name", "Camila"),
        phone=extra.pop("phone", "+573000000001"),
        country=extra.pop("country", "CO"),
        **extra,
    )


# Insert one plantilla, sendable by default: active (the account's own
# toggle) and "aceptada" (WhatsApp's verdict). body_sample_values is the JSON
# list of samples, element i pairing with {{i+1}}, so "Camila" is the
# fallback for {{1}}. The model is unique on (name, language), so a test that
# creates a second plantilla must pass a different name.
def make_template(**extra):
    return MessageTemplate.objects.create(
        name=extra.pop("name", "saludo_inicial"),
        body=extra.pop("body", "Hola {{1}}, gracias por escribirnos."),
        body_sample_values=extra.pop("body_sample_values", ["Camila"]),
        category=extra.pop("category", "marketing"),
        status=extra.pop("status", "aceptada"),
        **extra,
    )


# services.send_template end to end, in four groups: that a closed window is
# no obstacle to a plantilla, what the provider is handed, what the send is
# billed, and how refusals and provider failures leave the data.
#
# Pinned for every test in the class, so the amounts asserted below do not
# depend on the developer's own .env.
@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_CURRENCY="USD")
class SendTemplateTests(TestCase):
    # Runs before each test method, inside that method's own transaction.
    # conversation_for_client finds no thread for this brand-new client and
    # so creates one: a WhatsApp Conversation with no messages and, crucially
    # for these tests, no last_inbound_at -- the customer has never written.
    def setUp(self):
        self.client_row = make_client()
        self.template = make_template()
        self.conversation = services.conversation_for_client(self.client_row)

    # --- The point: a closed window is not a wall for templates -----------

    # The whole reason the feature exists. is_within_24h_window is False
    # (last_inbound_at is None), which would stop send_message dead; a
    # template goes out anyway. provider_message_id is the id the fake
    # provider handed back, so asserting it is truthy proves the provider
    # leg actually ran rather than being skipped.
    def test_sends_even_though_the_window_was_never_open(self):
        self.assertFalse(self.conversation.is_within_24h_window)

        message = services.send_template(self.conversation, self.template)

        self.assertEqual(message.direction, Message.OUTBOUND)
        self.assertEqual(message.body, "Hola Camila, gracias por escribirnos.")
        self.assertTrue(message.provider_message_id)
        self.assertEqual(message.template, self.template)

    # A client typed into the CRM by hand has no thread at all, so there
    # would be nowhere to put the message: conversation_for_client is what
    # creates one. The count assertion is the interesting half -- one thread,
    # not one per send.
    def test_a_brand_new_client_gets_a_conversation_to_receive_it(self):
        stranger = make_client(first_name="Nuevo", phone="+573000000777")
        self.assertFalse(Conversation.objects.filter(contact=stranger).exists())

        conversation = services.conversation_for_client(stranger)
        services.send_template(conversation, self.template)

        self.assertEqual(Conversation.objects.filter(contact=stranger).count(), 1)
        self.assertEqual(conversation.messages.count(), 1)

    # Called a second time for a client who already has an open thread, the
    # same row comes back (compared by primary key), so a template send and
    # the customer's later reply share one conversation.
    def test_an_existing_open_thread_is_reused_rather_than_duplicated(self):
        again = services.conversation_for_client(self.client_row)
        self.assertEqual(again.pk, self.conversation.pk)

    # ``values`` is keyed by the variable's number as an int: {1: ...} fills
    # {{1}}. What the agent typed beats the plantilla's stored sample.
    def test_typed_values_win_over_the_samples(self):
        message = services.send_template(self.conversation, self.template, {1: "Andrés"})
        self.assertEqual(message.body, "Hola Andrés, gracias por escribirnos.")

    # fill_body strips the supplied text, so whitespace counts as nothing
    # supplied and the sample ("Camila") is used -- a field left blank sends
    # a sensible word rather than a gap.
    def test_a_blank_value_falls_back_to_the_sample(self):
        message = services.send_template(self.conversation, self.template, {1: "  "})
        self.assertEqual(message.body, "Hola Camila, gracias por escribirnos.")

    # sent_by is only filled for an authenticated user; a real User instance
    # reports is_authenticated as True, so it is stored.
    def test_records_who_sent_it(self):
        user = get_user_model().objects.create_user("asesor")
        message = services.send_template(self.conversation, self.template, user=user)
        self.assertEqual(message.sent_by, user)

    # Conversation.Meta.ordering is "-last_message_at", so setting that
    # column is what lifts the thread to the top of the Inbox list.
    # refresh_from_db re-reads the row, so the assertion is about what was
    # stored rather than about the in-memory object.
    def test_the_thread_moves_to_the_top_of_the_list(self):
        services.send_template(self.conversation, self.template)
        self.conversation.refresh_from_db()
        self.assertIsNotNone(self.conversation.last_message_at)

    def test_sending_does_not_open_the_customer_window(self):
        # Only the customer writing back does that -- our own send must not
        # unlock free-form replies.
        # Mechanically: send_template touches last_message_at and leaves
        # last_inbound_at alone, and is_within_24h_window reads only the
        # latter.
        services.send_template(self.conversation, self.template)
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.is_within_24h_window)

    # --- What the provider is asked to do ---------------------------------

    # The contract between services and any provider adapter. patch.object
    # replaces the ``get_provider`` name *inside the services module* (which
    # imported it directly) for the duration of the with-block, so the call
    # site sees the Mock; return_value is what the fake send_template answers
    # with, standing in for a provider message id. call_args.kwargs is the
    # keyword arguments of the last call, which is how services calls it.
    def test_the_provider_gets_the_name_the_values_and_the_rendered_body(self):
        provider = Mock()
        provider.send_template.return_value = "prov-1"
        with patch.object(services, "get_provider", return_value=provider):
            services.send_template(self.conversation, self.template, {1: "Andrés"})

        kwargs = provider.send_template.call_args.kwargs
        self.assertEqual(kwargs["to"], "+573000000001")
        self.assertEqual(kwargs["template_name"], "saludo_inicial")
        # Numbers arrive as int keys and reach the provider as strings.
        self.assertEqual(kwargs["params"]["1"], "Andrés")
        # A reserved key of the provider contract, from MessageTemplate.language
        # (default "es"). Meta puts it in its payload; Baileys ignores it.
        self.assertEqual(kwargs["params"]["_language"], "es")
        # Providers with no template mechanism (Baileys) send this verbatim.
        self.assertEqual(
            kwargs["params"]["_body"], "Hola Andrés, gracias por escribirnos."
        )

    # --- Money ------------------------------------------------------------

    # The three billed_* columns are copied from the pricing.Quote in the
    # same INSERT that stores the text, so a row never exists half-priced.
    def test_the_price_is_frozen_onto_the_message(self):
        message = services.send_template(self.conversation, self.template)
        self.assertEqual(message.billed_amount, Decimal("0.0125"))
        self.assertEqual(message.billed_currency, "USD")
        self.assertEqual(message.billed_category, "marketing")

    # "Frozen" spelled out: the rates change under the row, refresh_from_db
    # re-reads it from the database, and the stored amount is still the old
    # one -- nothing re-prices history.
    def test_a_later_rate_change_does_not_rewrite_what_a_send_cost(self):
        message = services.send_template(self.conversation, self.template)
        with override_settings(
            MESSAGING_TEMPLATE_RATES=json.dumps({"CO": {"marketing": "9.99"}})
        ):
            message.refresh_from_db()
            self.assertEqual(message.billed_amount, Decimal("0.0125"))

    # Opening the window by hand: last_inbound_at set to now means the
    # customer wrote a moment ago. save(update_fields=[...]) writes only that
    # column. A utility plantilla then rides the free service conversation,
    # and zero is *recorded* -- not left NULL, which would mean "not billed".
    def test_a_utility_template_inside_the_window_is_recorded_as_free(self):
        self.conversation.last_inbound_at = timezone.now()
        self.conversation.save(update_fields=["last_inbound_at"])
        utility = make_template(name="pedido_en_camino", category="utility")

        message = services.send_template(self.conversation, utility)

        self.assertEqual(message.billed_amount, Decimal("0"))

    # The ceiling is enforced in the service, not in the UI. The first send
    # is real (it spends 0.0125 of the 0.02 cap); the second would reach
    # 0.025 and is refused. The Mock provider is in place for the second one
    # only to prove the refusal happens *before* the provider is reached --
    # assert_not_called is the point of the test, and the unchanged message
    # count shows no row was written either.
    @override_settings(MESSAGING_MONTHLY_BUDGET="0.02")
    def test_the_monthly_budget_refuses_the_send_before_it_costs_anything(self):
        services.send_template(self.conversation, self.template)  # 0.0125 spent
        provider = Mock()
        with patch.object(services, "get_provider", return_value=provider):
            with self.assertRaises(services.BudgetExceeded):
                services.send_template(self.conversation, self.template)

        provider.send_template.assert_not_called()
        self.assertEqual(Message.objects.count(), 1)

    # --- Refusals and failures --------------------------------------------

    # is_active is the account's own on/off switch. assertRaises as a context
    # manager passes only if the block raises that class; the count assertion
    # adds what the exception alone does not say -- no row, so nothing to
    # bill and nothing in the thread.
    def test_a_deactivated_template_is_refused_and_nothing_is_written(self):
        off = make_template(name="apagada", is_active=False)
        with self.assertRaises(services.TemplateNotSendable):
            services.send_template(self.conversation, off)
        self.assertEqual(Message.objects.count(), 0)

    # The other half of the same guard: status is WhatsApp's verdict, and
    # "rechazada" would bounce, so it is refused here rather than paid for.
    def test_a_rejected_template_is_refused(self):
        rejected = make_template(name="rechazada_x", status="rechazada")
        with self.assertRaises(services.TemplateNotSendable):
            services.send_template(self.conversation, rejected)
        self.assertEqual(Message.objects.count(), 0)

    # side_effect makes the mocked provider raise instead of returning, which
    # is how any real adapter reports a network or API error. The row was
    # already written by then, so it stays -- marked failed, with the amount
    # zeroed, because WhatsApp bills nothing for a message it never took.
    # Message.objects.get() with no arguments returns the one row and would
    # raise if there were none or several, so it doubles as a count check.
    def test_a_failed_send_keeps_the_row_but_bills_nothing(self):
        provider = Mock()
        provider.send_template.side_effect = RuntimeError("boom")
        with patch.object(services, "get_provider", return_value=provider):
            with self.assertRaises(services.SendFailed):
                services.send_template(self.conversation, self.template)

        message = Message.objects.get()
        self.assertEqual(message.status, MessageStatus.FAILED.value)
        self.assertEqual(message.billed_amount, Decimal("0"))


# What the send dialog's plantilla picker is built from. No override_settings
# here: this is a queryset question, with no money in it.
class SendableTemplatesTests(TestCase):
    # Four plantillas covering every combination that matters, then one
    # assertion per rule: aceptadas and pendientes are offered, rechazadas
    # and deactivated ones are not. The final assertEqual on the whole list
    # also pins the order, which the QuerySet gets from order_by("status",
    # "name") -- "aceptada" sorts before "pendiente".
    def test_offers_active_templates_that_were_not_rejected(self):
        make_template(name="aceptada_x", status="aceptada")
        make_template(name="pendiente_x", status="pendiente")
        make_template(name="rechazada_x", status="rechazada")
        make_template(name="apagada_x", status="aceptada", is_active=False)

        # sendable_templates() returns a lazy QuerySet; iterating it here is
        # what runs the query.
        names = [entry.name for entry in services.sendable_templates()]

        self.assertIn("aceptada_x", names)
        self.assertNotIn("rechazada_x", names)
        self.assertNotIn("apagada_x", names)
        # Pendientes stay on the list (this MVP has no approval pipeline) but
        # behind the approved ones.
        self.assertEqual(names, ["aceptada_x", "pendiente_x"])


# The read-only lookup the send dialog uses: it needs to know whether a
# thread (and so a 24h window) exists without bringing one into being.
class FindOpenConversationTests(TestCase):
    # A resolved thread is closed history, so it is not offered for reuse:
    # the lookup answers None, and conversation_for_client then opens a
    # second row rather than reopening the first -- which is what keeps
    # per-thread metrics intact when a customer comes back.
    def test_a_resolved_thread_is_not_reused(self):
        contact = make_client()
        Conversation.objects.create(contact=contact, status=Conversation.RESOLVED)

        self.assertIsNone(services.find_open_conversation(contact))

        fresh = services.conversation_for_client(contact)
        self.assertNotEqual(fresh.status, Conversation.RESOLVED)
        self.assertEqual(Conversation.objects.filter(contact=contact).count(), 2)

    # The property the dialog depends on: opening it must not litter the
    # Inbox with empty threads, so merely looking creates nothing.
    def test_looking_does_not_create(self):
        contact = make_client()
        self.assertIsNone(services.find_open_conversation(contact))
        self.assertEqual(Conversation.objects.count(), 0)
