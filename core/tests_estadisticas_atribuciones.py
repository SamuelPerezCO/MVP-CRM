"""Tests for the Estadísticas > Atribuciones panel: per-channel attribution
of conversations and sales, the tiles (canal principal, mejor conversión),
the period window, and the empty state."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import estadisticas_atribuciones as atribuciones
from core.estadisticas_periodos import DEFAULT_PERIOD, PERIOD_BY_KEY
from core.models import Client
from messaging import services
from messaging.models import Conversation, Tag


def make_conversation(phone: str, channel="whatsapp", days_ago=0) -> Conversation:
    contact = Client.objects.create(first_name=f"C{phone[-2:]}", phone=phone)
    conversation = Conversation.objects.create(
        contact=contact, channel=channel, last_message_at=timezone.now()
    )
    if days_ago:
        Conversation.objects.filter(pk=conversation.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
    return conversation


def section_url() -> str:
    return reverse("section", args=["estadisticas"]) + "?view=atribuciones"


class AtribucionesReportTests(TestCase):
    def setUp(self):
        self.venta = Tag.objects.create(name="VENTA EFECTIVA", color="green")

    def report(self, period_key=DEFAULT_PERIOD):
        return atribuciones.report(PERIOD_BY_KEY[period_key])

    def test_rows_carry_conversations_sales_conversion_and_share(self):
        for i in range(3):
            chat = make_conversation(f"+5730000005{i:02d}", "whatsapp")
        services.apply_tag([chat], self.venta)  # 1 of the 3 whatsapp chats
        make_conversation("+573000000510", "instagram-dm")

        report = self.report()
        self.assertEqual(
            [
                (r.label, r.conversations, r.sales, r.conversion_pct, r.share_pct)
                for r in report["rows"]
            ],
            [("WhatsApp", 3, 1, 33, 75), ("Instagram DM", 1, 0, 0, 25)],
        )
        self.assertEqual(report["total_conversations"], 4)
        self.assertEqual(report["total_sales"], 1)
        self.assertEqual(report["max_conversations"], 3)

    def test_two_sale_tags_on_one_chat_is_one_sale(self):
        chat = make_conversation("+573000000520")
        mayorista = Tag.objects.create(name="Venta mayorista", color="teal")
        services.apply_tag([chat], self.venta)
        services.apply_tag([chat], mayorista)
        self.assertEqual(self.report()["rows"][0].sales, 1)

    def test_top_is_the_busiest_channel_and_best_conversion_needs_a_sale(self):
        for i in range(2):
            make_conversation(f"+5730000005{30 + i}", "whatsapp")
        sold = make_conversation("+573000000540", "messenger")
        services.apply_tag([sold], self.venta)

        report = self.report()
        self.assertEqual(report["top"].label, "WhatsApp")
        # Messenger converts 100% of its one chat; WhatsApp converts none.
        self.assertEqual(report["best_conversion"].label, "Messenger")

    def test_no_sales_means_no_best_conversion(self):
        make_conversation("+573000000550")
        self.assertIsNone(self.report()["best_conversion"])

    def test_period_window_filters_by_creation_date(self):
        make_conversation("+573000000560", "whatsapp", days_ago=45)
        make_conversation("+573000000561", "messenger")
        month = self.report("30")
        self.assertEqual([r.label for r in month["rows"]], ["Messenger"])
        everything = self.report("todo")
        self.assertEqual(len(everything["rows"]), 2)

    def test_empty_period_reports_no_rows(self):
        report = self.report()
        self.assertEqual(report["rows"], [])
        self.assertIsNone(report["top"])
        self.assertEqual(report["max_conversations"], 0)


class AtribucionesPanelTests(TestCase):
    def setUp(self):
        self.venta = Tag.objects.create(name="VENTA EFECTIVA", color="green")
        chat = make_conversation("+573000000570", "whatsapp")
        services.apply_tag([chat], self.venta)
        make_conversation("+573000000571", "instagram-dm")

    def test_panel_is_real_not_the_placeholder(self):
        response = self.client.get(section_url())
        self.assertEqual(response.context["active_view"], "atribuciones")
        self.assertEqual(
            response.context["panel_template"],
            "partials/estadisticas/panels/atribuciones.html",
        )
        self.assertContains(response, "De dónde vienen tus conversaciones")
        self.assertNotContains(response, "próximamente")

    def test_title_carries_the_alpha_badge(self):
        html = self.client.get(section_url()).content.decode()
        panel = html.split("stats-page__title", 1)[1].split("</h1>", 1)[0]
        self.assertIn(">Alpha</span>", panel)

    def test_tiles_and_table_render(self):
        html = self.client.get(section_url()).content.decode()
        self.assertIn("Canal principal", html)
        self.assertIn("Ventas atribuidas", html)
        self.assertIn("Mejor conversión", html)
        self.assertIn('table__name">WhatsApp</td>', html)
        self.assertIn('table__name">Instagram DM</td>', html)
        self.assertIn("width: 100%", html)

    def test_unknown_period_falls_back_to_the_default(self):
        response = self.client.get(section_url() + "&period=bogus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"].key, DEFAULT_PERIOD)

    def test_panel_endpoint_honors_the_period_param(self):
        Conversation.objects.update(
            created_at=timezone.now() - timedelta(days=60)
        )
        response = self.client.get(
            reverse("estadisticas_panel", args=["atribuciones"]), {"period": "7"}
        )
        body = response.content.decode()
        self.assertNotIn("<html", body)
        self.assertIn("Sin conversaciones que atribuir", body)
        self.assertIn('value="7" selected', body)

    def test_no_conversations_renders_the_empty_state(self):
        Conversation.objects.all().delete()
        response = self.client.get(section_url())
        self.assertContains(response, "Sin conversaciones que atribuir")
        self.assertContains(response, 'name="period"')
