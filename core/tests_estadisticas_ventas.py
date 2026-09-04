"""Tests for the Estadísticas > Ventas panel: what counts as a sale (tags
whose name contains «venta»), the period window over tagged_at, the
conversion tile and the per-channel table, plus the setup empty state."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import estadisticas_ventas as ventas
from core.estadisticas_periodos import DEFAULT_PERIOD, PERIOD_BY_KEY
from core.models import Client
from messaging import services
from messaging.models import Conversation, ConversationTag, Tag


def make_conversation(phone: str, channel: str = "whatsapp") -> Conversation:
    contact = Client.objects.create(first_name=f"C{phone[-2:]}", phone=phone)
    return Conversation.objects.create(
        contact=contact, channel=channel, last_message_at=timezone.now()
    )


def mark_sale(conversation, tag, days_ago=0):
    services.apply_tag([conversation], tag)
    if days_ago:
        ConversationTag.objects.filter(conversation=conversation, tag=tag).update(
            tagged_at=timezone.now() - timedelta(days=days_ago)
        )


def section_url() -> str:
    return reverse("section", args=["estadisticas"]) + "?view=ventas"


class VentasReportTests(TestCase):
    def setUp(self):
        self.venta = Tag.objects.create(name="VENTA EFECTIVA", color="green")
        self.mayorista = Tag.objects.create(name="Venta mayorista", color="teal")
        self.otra = Tag.objects.create(name="CLIENTE NUEVO", color="blue")

    def report(self, period_key=DEFAULT_PERIOD):
        return ventas.report(PERIOD_BY_KEY[period_key])

    def test_only_venta_named_tags_count(self):
        chat = make_conversation("+573000000301")
        services.apply_tag([chat], self.otra)
        self.assertEqual(self.report()["sales"], 0)
        mark_sale(chat, self.venta)
        self.assertEqual(self.report()["sales"], 1)

    def test_two_sale_tags_on_one_chat_is_one_sale(self):
        chat = make_conversation("+573000000302")
        mark_sale(chat, self.venta)
        mark_sale(chat, self.mayorista)
        report = self.report()
        self.assertEqual(report["sales"], 1)
        self.assertEqual(report["total_sales"], 1)

    def test_sales_are_dated_by_when_they_were_tagged(self):
        old = make_conversation("+573000000303")
        Conversation.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=60)
        )
        mark_sale(old, self.venta, days_ago=45)
        recent = make_conversation("+573000000304")
        mark_sale(recent, self.venta, days_ago=2)

        month = self.report("30")
        self.assertEqual(month["sales"], 1)          # only the recent one
        self.assertEqual(month["total_sales"], 2)    # history keeps both
        everything = self.report("todo")
        self.assertEqual(everything["sales"], 2)

    def test_conversion_divides_sales_by_conversations_started(self):
        for i in range(4):
            make_conversation(f"+57300000031{i}")
        sold = make_conversation("+573000000319")
        mark_sale(sold, self.venta)
        report = self.report()
        self.assertEqual(report["conversations_started"], 5)
        self.assertEqual(report["conversion_pct"], 20)

    def test_no_new_conversations_means_no_rate_not_zero(self):
        self.assertIsNone(self.report()["conversion_pct"])

    def test_channels_rank_by_sales(self):
        for i in range(2):
            mark_sale(make_conversation(f"+5730000003{i}0", "whatsapp"), self.venta)
        mark_sale(make_conversation("+573000000330", "instagram-dm"), self.venta)
        report = self.report()
        self.assertEqual(
            [(row.label, row.sales) for row in report["channels"]],
            [("WhatsApp", 2), ("Instagram DM", 1)],
        )
        self.assertEqual(report["max_channel_sales"], 2)

    def test_archived_sale_tag_still_counts_its_history(self):
        chat = make_conversation("+573000000340")
        mark_sale(chat, self.venta)
        services.set_tag_archived(self.venta, True)
        self.assertEqual(self.report()["sales"], 1)


class VentasPanelTests(TestCase):
    def setUp(self):
        self.venta = Tag.objects.create(name="VENTA EFECTIVA", color="green")
        self.chat = make_conversation("+573000000350")
        mark_sale(self.chat, self.venta)

    def test_panel_is_real_not_the_placeholder(self):
        response = self.client.get(section_url())
        self.assertEqual(response.context["active_view"], "ventas")
        self.assertEqual(
            response.context["panel_template"],
            "partials/estadisticas/panels/ventas.html",
        )
        self.assertContains(response, "Estadísticas de ventas")
        self.assertNotContains(response, "próximamente")

    def test_sale_tags_render_as_pills_next_to_the_filter(self):
        response = self.client.get(section_url())
        self.assertContains(response, "Cuentan como venta:")
        self.assertContains(response, 'tag-pill--green">VENTA EFECTIVA</span>')

    def test_tiles_and_channel_table_render(self):
        html = self.client.get(section_url()).content.decode()
        self.assertIn("Ventas del período", html)
        self.assertIn("Tasa de conversión", html)
        self.assertIn("Total histórico", html)
        self.assertIn('table__name">WhatsApp</td>', html)
        self.assertIn("width: 100%", html)

    def test_unknown_period_falls_back_to_the_default(self):
        response = self.client.get(section_url() + "&period=bogus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"].key, DEFAULT_PERIOD)

    def test_panel_endpoint_honors_the_period_param(self):
        ConversationTag.objects.update(tagged_at=timezone.now() - timedelta(days=60))
        response = self.client.get(
            reverse("estadisticas_panel", args=["ventas"]), {"period": "7"}
        )
        body = response.content.decode()
        self.assertNotIn("<html", body)
        self.assertIn("Sin ventas en el período", body)
        self.assertIn('value="7" selected', body)

    def test_no_sales_in_period_keeps_tiles_but_swaps_the_table(self):
        ConversationTag.objects.update(tagged_at=timezone.now() - timedelta(days=60))
        response = self.client.get(section_url())
        self.assertContains(response, "Ventas del período")
        self.assertContains(response, "Sin ventas en el período")
        # History is still visible in the third tile's count.
        self.assertEqual(response.context["report"]["total_sales"], 1)


class VentasSetupStateTests(TestCase):
    def test_no_sale_tag_renders_the_workflow_teaching_state(self):
        Tag.objects.create(name="CLIENTE NUEVO", color="blue")  # not a sale tag
        response = self.client.get(section_url())
        self.assertContains(response, "Empieza a registrar ventas")
        crm_url = reverse("section", args=["crm"]) + "?view=etiquetas"
        self.assertContains(response, f'href="{crm_url}"')
        # No filter or tiles until the workflow exists.
        self.assertNotContains(response, 'name="period"')
        self.assertNotContains(response, "Ventas del período")
