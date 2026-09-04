"""Tests for reconciling what the CRM estimated against what Meta charged.

The CRM prices a template send before it goes out; Meta decides afterwards,
on delivery, at the category *it* assigned. Its status webhook carries a
``pricing`` object saying which rate bucket applied -- never an amount -- and
these tests pin what the CRM does with that.

The payloads here are Meta's own documented examples, kept verbatim so a
change in their shape shows up as a failing test rather than as silently
wrong money.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import Client, MessageTemplate

from . import services
from .models import Conversation, Message
from .providers.meta import MetaProvider, _parse_pricing
from .providers.types import InboundEvent, MessageStatus

WAMID = "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQUFERjg0NDEzNDdFODU3MUMxMAA="

# Meta's own example of a `sent` status under per-message pricing.
SENT_STATUS_PMP = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "102290129340398", "changes": [{"value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881",
                     "phone_number_id": "106540352242922"},
        "statuses": [{
            "id": WAMID,
            "status": "sent",
            "timestamp": "1750030073",
            "recipient_id": "573000000001",
            "conversation": {"id": "72b14d6bd5407799e66f64d1b338e567",
                             "expiration_timestamp": "1750116480",
                             "origin": {"type": "marketing"}},
            "pricing": {"billable": True, "pricing_model": "PMP",
                        "type": "regular", "category": "marketing"},
        }],
    }, "field": "messages"}]}],
}


def pricing_status(**pricing):
    """One `delivered` status carrying the given pricing object."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "1", "phone_number_id": "1"},
            "statuses": [{
                "id": WAMID,
                "status": "delivered",
                "timestamp": "1750030073",
                "recipient_id": "573000000001",
                "pricing": pricing,
            }],
        }, "field": "messages"}]}],
    }


class ParsePricingTests(TestCase):
    def test_reads_metas_documented_sent_payload(self):
        request = type("R", (), {"body": json.dumps(SENT_STATUS_PMP).encode()})()
        events = MetaProvider().parse_webhook(request)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, "status")
        self.assertEqual(event.status, MessageStatus.SENT)
        self.assertEqual(
            event.pricing,
            {"billable": True, "model": "PMP", "category": "marketing", "type": "regular"},
        )

    def test_a_receipt_without_pricing_carries_none(self):
        # Most receipts have no pricing object at all.
        self.assertIsNone(_parse_pricing(None))
        self.assertIsNone(_parse_pricing({}))
        self.assertIsNone(_parse_pricing("not an object"))

    def test_a_partial_pricing_object_still_parses(self):
        # Meta's own legacy examples ship a pricing object with no `type`.
        self.assertEqual(
            _parse_pricing({"billable": True, "pricing_model": "CBP", "category": "service"}),
            {"billable": True, "model": "CBP", "category": "service", "type": ""},
        )

    def test_the_hyphenated_category_is_kept_verbatim(self):
        # Meta spells this with a hyphen in webhooks and an underscore on the
        # analytics endpoint; normalising here would hide the mismatch.
        parsed = _parse_pricing({"category": "authentication-international"})
        self.assertEqual(parsed["category"], "authentication-international")


RATES = json.dumps(
    {"Colombia": {"marketing": "0.0125", "utility": "0.0022", "authentication": "0.0077"}}
)


@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_CURRENCY="USD")
class ReconcileTests(TestCase):
    def setUp(self):
        self.contact = Client.objects.create(
            first_name="Camila", phone="+573000000001", country="CO"
        )
        self.conversation = Conversation.objects.create(contact=self.contact)
        self.template = MessageTemplate.objects.create(
            name="saludo_inicial", body="Hola", category="utility", status="aceptada"
        )

    def send(self, **overrides):
        """A billed outbound message, as send_template would leave it."""
        fields = {
            "conversation": self.conversation,
            "direction": Message.OUTBOUND,
            "body": "Hola",
            "provider_message_id": WAMID,
            "status": MessageStatus.SENT.value,
            "template": self.template,
            "billed_category": "utility",
            "billed_amount": Decimal("0.0022"),
            "billed_currency": "USD",
        }
        fields.update(overrides)
        return Message.objects.create(**fields)

    def deliver(self, **pricing):
        services.process_inbound_events(
            MetaProvider().parse_webhook(
                type("R", (), {"body": json.dumps(pricing_status(**pricing)).encode()})()
            )
        )

    def test_metas_verdict_is_recorded_on_the_message(self):
        message = self.send()
        self.deliver(billable=True, pricing_model="PMP", type="regular", category="utility")

        message.refresh_from_db()
        self.assertEqual(message.meta_pricing_type, "regular")
        self.assertEqual(message.meta_pricing_category, "utility")
        self.assertEqual(message.meta_pricing_model, "PMP")
        self.assertIs(message.meta_billable, True)
        self.assertTrue(message.cost_is_confirmed)

    def test_a_confirmed_charge_leaves_the_estimate_alone(self):
        message = self.send()
        self.deliver(billable=True, pricing_model="PMP", type="regular", category="utility")

        message.refresh_from_db()
        self.assertEqual(message.billed_amount, Decimal("0.0022"))
        self.assertEqual(message.billed_category, "utility")

    def test_a_free_entry_point_send_is_refunded_to_zero(self):
        # The CRM cannot see the 72-hour window a Click-to-WhatsApp ad opens,
        # so it quotes the send as billable; Meta reports it free.
        message = self.send()
        self.deliver(billable=False, pricing_model="PMP", type="free_entry_point",
                     category="marketing")

        message.refresh_from_db()
        self.assertEqual(message.billed_amount, Decimal("0"))
        self.assertEqual(message.meta_pricing_type, "free_entry_point")

    def test_a_free_customer_service_send_is_refunded_to_zero(self):
        message = self.send()
        self.deliver(billable=False, pricing_model="PMP", type="free_customer_service",
                     category="utility")

        message.refresh_from_db()
        self.assertEqual(message.billed_amount, Decimal("0"))

    def test_billable_false_is_honoured_when_type_is_missing(self):
        # `type` is absent from some payloads; `billable` still decides.
        message = self.send()
        self.deliver(billable=False, pricing_model="CBP", category="service")

        message.refresh_from_db()
        self.assertEqual(message.billed_amount, Decimal("0"))

    def test_a_recategorised_template_is_repriced_at_metas_category(self):
        # Meta re-categorises templates on its own, and bills at its own
        # verdict: a utility send judged marketing costs marketing money.
        message = self.send()
        self.deliver(billable=True, pricing_model="PMP", type="regular",
                     category="marketing")

        message.refresh_from_db()
        self.assertEqual(message.billed_category, "marketing")
        self.assertEqual(message.billed_amount, Decimal("0.0125"))

    def test_a_category_the_crm_cannot_price_is_left_as_estimated(self):
        # marketing_lite and referral_conversion are billed by mechanisms
        # this CRM does not implement.
        message = self.send()
        self.deliver(billable=True, pricing_model="PMP", type="regular",
                     category="marketing_lite")

        message.refresh_from_db()
        self.assertEqual(message.billed_amount, Decimal("0.0022"))
        self.assertEqual(message.meta_pricing_category, "marketing_lite")

    def test_an_unbilled_message_is_never_given_an_amount(self):
        # A free-form reply carries NULL and must stay NULL.
        message = self.send(billed_amount=None, billed_category="", template=None)
        self.deliver(billable=True, pricing_model="PMP", type="regular",
                     category="service")

        message.refresh_from_db()
        self.assertIsNone(message.billed_amount)
        self.assertEqual(message.meta_pricing_category, "service")

    def test_a_failed_send_keeps_its_zero(self):
        message = self.send(status=MessageStatus.FAILED.value, billed_amount=Decimal("0"))
        self.deliver(billable=True, pricing_model="PMP", type="regular",
                     category="marketing")

        message.refresh_from_db()
        self.assertEqual(message.billed_amount, Decimal("0"))

    def test_pricing_is_applied_even_when_the_status_cannot_move_forward(self):
        # Meta puts pricing on `sent` and on one of delivered/read, and they
        # arrive in any order. A late `sent` still carries a verdict.
        message = self.send(status=MessageStatus.READ.value)
        request = type("R", (), {"body": json.dumps(SENT_STATUS_PMP).encode()})()
        services.process_inbound_events(MetaProvider().parse_webhook(request))

        message.refresh_from_db()
        self.assertEqual(message.status, MessageStatus.READ.value)  # not regressed
        self.assertEqual(message.meta_pricing_type, "regular")

    def test_a_receipt_for_an_unknown_message_is_ignored(self):
        self.deliver(billable=True, pricing_model="PMP", type="regular", category="utility")
        self.assertEqual(Message.objects.count(), 0)
