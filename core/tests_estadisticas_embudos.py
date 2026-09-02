"""Tests for the Estadísticas > Embudos panel: the four-stage conversation
funnel (creadas, respondidas, resueltas, con venta), its period window, and
the empty state."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import estadisticas_embudos as embudos
from core.estadisticas_periodos import DEFAULT_PERIOD, PERIOD_BY_KEY
from core.models import Client
from messaging import services
from messaging.models import Conversation, Message, Tag


def make_conversation(phone: str, days_ago=0) -> Conversation:
    contact = Client.objects.create(first_name=f"C{phone[-2:]}", phone=phone)
    conversation = Conversation.objects.create(
        contact=contact, channel="whatsapp", last_message_at=timezone.now()
    )
    if days_ago:
        Conversation.objects.filter(pk=conversation.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
    return conversation


def answer(conversation):
    Message.objects.create(
        conversation=conversation,
        direction=Message.OUTBOUND,
        body="respuesta",
        provider_message_id=f"embudo-{conversation.pk}-{Message.objects.count()}",
    )


def section_url() -> str:
    return reverse("section", args=["estadisticas"]) + "?view=embudos"


def stage(report, key):
    return next(s for s in report["stages"] if s.key == key)


class EmbudosReportTests(TestCase):
    def setUp(self):
        self.venta = Tag.objects.create(name="VENTA EFECTIVA", color="green")

    def report(self, period_key=DEFAULT_PERIOD):
        return embudos.report(PERIOD_BY_KEY[period_key])

    def test_stages_count_their_own_rule(self):
        # 4 created; 3 answered; 2 resolved; 1 sold.
        chats = [make_conversation(f"+5730000004{i:02d}") for i in range(4)]
        for chat in chats[:3]:
            answer(chat)
        Conversation.objects.filter(
            pk__in=[chats[0].pk, chats[1].pk]
        ).update(status=Conversation.RESOLVED)
        services.apply_tag([chats[0]], self.venta)

        report = self.report()
        self.assertEqual(
            [(s.key, s.count, s.pct) for s in report["stages"]],
            [
                ("creadas", 4, 100),
                ("respondidas", 3, 75),
                ("resueltas", 2, 50),
                ("venta", 1, 25),
            ],
        )

    def test_two_outbound_messages_still_count_one_conversation(self):
        chat = make_conversation("+573000000410")
        answer(chat)
        answer(chat)
        self.assertEqual(stage(self.report(), "respondidas").count, 1)

    def test_period_window_filters_by_creation_date(self):
        old = make_conversation("+573000000420", days_ago=45)
        answer(old)
        make_conversation("+573000000421")
        month = self.report("30")
        self.assertEqual(stage(month, "creadas").count, 1)
        self.assertEqual(stage(month, "respondidas").count, 0)
        everything = self.report("todo")
        self.assertEqual(stage(everything, "creadas").count, 2)
        self.assertEqual(stage(everything, "respondidas").count, 1)

    def test_venta_stage_uses_the_sale_tag_convention(self):
        chat = make_conversation("+573000000430")
        other = Tag.objects.create(name="CLIENTE NUEVO", color="blue")
        services.apply_tag([chat], other)
        self.assertEqual(stage(self.report(), "venta").count, 0)
        services.apply_tag([chat], self.venta)
        self.assertEqual(stage(self.report(), "venta").count, 1)

    def test_empty_period_reports_a_zero_base(self):
        report = self.report()
        self.assertEqual(report["base"], 0)
        self.assertEqual(
            [(s.count, s.pct) for s in report["stages"]], [(0, 0)] * 4
        )


class EmbudosPanelTests(TestCase):
    def setUp(self):
        self.chat = make_conversation("+573000000440")
        answer(self.chat)

    def test_panel_is_real_not_the_placeholder(self):
        response = self.client.get(section_url())
        self.assertEqual(response.context["active_view"], "embudos")
        self.assertEqual(
            response.context["panel_template"],
            "partials/estadisticas/panels/embudos.html",
        )
        self.assertContains(response, "Embudo de conversión")
        self.assertNotContains(response, "próximamente")

    def test_every_stage_renders_with_its_bar(self):
        html = self.client.get(section_url()).content.decode()
        for label in [
            "Conversaciones creadas",
            "Respondidas por el equipo",
            "Resueltas",
            "Con venta",
        ]:
            with self.subTest(label):
                self.assertIn(label, html)
        # Two stages have counts (created + answered) -> two fills; the
        # zero-count stages render an empty track.
        self.assertEqual(html.count("funnel__fill "), 2)
        self.assertIn("width: 100%", html)

    def test_unknown_period_falls_back_to_the_default(self):
        response = self.client.get(section_url() + "&period=bogus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"].key, DEFAULT_PERIOD)

    def test_panel_endpoint_honors_the_period_param(self):
        Conversation.objects.filter(pk=self.chat.pk).update(
            created_at=timezone.now() - timedelta(days=60)
        )
        response = self.client.get(
            reverse("estadisticas_panel", args=["embudos"]), {"period": "7"}
        )
        body = response.content.decode()
        self.assertNotIn("<html", body)
        self.assertIn("Sin conversaciones en el período", body)
        self.assertIn('value="7" selected', body)
        # A wider period brings the funnel back.
        response = self.client.get(
            reverse("estadisticas_panel", args=["embudos"]), {"period": "todo"}
        )
        self.assertIn("Conversaciones creadas", response.content.decode())

    def test_no_conversations_renders_the_empty_state(self):
        Conversation.objects.all().delete()
        response = self.client.get(section_url())
        self.assertContains(response, "Sin conversaciones en el período")
        self.assertContains(response, 'name="period"')
