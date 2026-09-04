"""Tests for the price an agent sees, and the refusal when it is too much.

The pricing subsystem itself is tested in messaging/tests_pricing.py. These
cover the wiring to the screen: the quote reaching the Enviar plantilla
dialog, and a send refused for cost coming back as a readable line on both
doors that send a plantilla rather than as a 500.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Client, MessageTemplate
from messaging.models import Conversation, Message

HTMX = {"HTTP_HX_REQUEST": "true"}

# A synthetic card so these assert exact numbers without pinning Meta's.
RATES = json.dumps(
    {"Colombia": {"marketing": "0.0125", "utility": "0.0022", "authentication": "0.0077"}}
)


def make_client(**extra):
    return Client.objects.create(
        first_name=extra.pop("first_name", "Camila"),
        phone=extra.pop("phone", "+573000000001"),
        country=extra.pop("country", "CO"),
        channel=extra.pop("channel", "whatsapp"),
        **extra,
    )


def make_template(**extra):
    return MessageTemplate.objects.create(
        name=extra.pop("name", "saludo_inicial"),
        body=extra.pop("body", "Hola {{1}}"),
        body_sample_values=extra.pop("body_sample_values", ["Camila"]),
        category=extra.pop("category", "marketing"),
        status=extra.pop("status", "aceptada"),
        **extra,
    )


@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_CURRENCY="USD")
class DialogShowsThePriceTests(TestCase):
    def setUp(self):
        self.contact = make_client()
        self.conversation = Conversation.objects.create(contact=self.contact)
        self.template = make_template()

    def dialog(self):
        return self.client.get(
            reverse("inbox_template_send", args=[self.conversation.pk]), **HTMX
        ).content.decode()

    def test_the_send_costs_what_the_card_says(self):
        html = self.dialog()
        self.assertIn("0.0125", html)
        self.assertIn("USD", html)
        # The market is named, so the agent can see which row priced it.
        self.assertIn("Colombia", html)

    def test_the_month_to_date_total_is_shown(self):
        html = self.dialog()
        self.assertIn("Plantillas enviadas este mes", html)
        # And it is an estimate until delivery -- said out loud.
        self.assertIn("estimación", html)

    def test_a_free_send_says_so_instead_of_a_price(self):
        # A utility plantilla inside an open window rides the service
        # conversation, which is free on the card in force.
        from django.utils import timezone

        self.conversation.last_inbound_at = timezone.now()
        self.conversation.save(update_fields=["last_inbound_at"])
        MessageTemplate.objects.all().delete()
        make_template(name="pedido_en_camino", category="utility")

        self.assertIn("Sin costo", self.dialog())

    def test_each_plantilla_carries_its_own_price(self):
        make_template(name="pedido_en_camino", category="utility")
        html = self.dialog()
        self.assertIn("0.0125", html)   # marketing
        self.assertIn("0.0022", html)   # utility


@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_MONTHLY_BUDGET="0.02")
class BudgetRefusalIsReadableTests(TestCase):
    """Over the month's ceiling is a refusal to read, not a crash -- on both
    doors that send a plantilla."""

    def setUp(self):
        from messaging import services

        self.contact = make_client()
        self.conversation = Conversation.objects.create(contact=self.contact)
        self.template = make_template()
        # Spend most of the ceiling, so the next send crosses it.
        services.send_template(self.conversation, self.template, {"1": "Ana"})

    def test_the_composer_dialog_shows_the_reason(self):
        response = self.client.post(
            reverse("inbox_template_send", args=[self.conversation.pk]),
            {"template": self.template.pk, f"var_{self.template.pk}_1": "Ana"},
            **HTMX,
        )
        self.assertLess(response.status_code, 500)
        self.assertIn("presupuesto mensual", response.content.decode())
        self.assertEqual(Message.objects.count(), 1)

    def test_nuevo_chat_shows_the_reason(self):
        response = self.client.post(
            reverse("inbox_new_chat"),
            {
                "cliente": self.contact.pk,
                "plantilla": self.template.pk,
                f"var_{self.template.pk}_1": "Ana",
            },
            **HTMX,
        )
        self.assertLess(response.status_code, 500)
        self.assertIn("presupuesto mensual", response.content.decode())
        self.assertEqual(Message.objects.count(), 1)


class RateOverrideIsReadTests(TestCase):
    """MESSAGING_TEMPLATE_RATES is documented in .env.example; it has to
    actually reach messaging.pricing, or the documentation is a lie."""

    @override_settings(
        MESSAGING_TEMPLATE_RATES=json.dumps({"Colombia": {"marketing": "9.99"}})
    )
    def test_the_override_changes_the_quote(self):
        from messaging import pricing

        self.assertEqual(pricing.rate_for("Colombia", "marketing"), Decimal("9.99"))

    def test_the_setting_exists_so_the_environment_can_set_it(self):
        from django.conf import settings

        # getattr with a default would hide a missing setting: pricing.rates()
        # would silently ignore the variable the .env.example tells people to
        # set. These must be real settings, not absent names.
        for name in (
            "MESSAGING_TEMPLATE_RATES",
            "MESSAGING_CURRENCY",
            "MESSAGING_MONTHLY_BUDGET",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(settings, name))
