"""Tests for the Estadísticas > Temas de conversación panel: the word
frequency report (tokenizer, accent folding, period window) and the panel's
rendering, filter and empty state."""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import estadisticas_temas as temas
from core.models import Client
from messaging.models import Conversation, Message


def make_conversation(phone: str) -> Conversation:
    contact = Client.objects.create(first_name=f"C{phone[-2:]}", phone=phone)
    return Conversation.objects.create(
        contact=contact, channel="whatsapp", last_message_at=timezone.now()
    )


def say(conversation, body, direction=Message.INBOUND, days_ago=0):
    return Message.objects.create(
        conversation=conversation,
        direction=direction,
        body=body,
        timestamp=timezone.now() - timedelta(days=days_ago),
        provider_message_id=f"tema-{conversation.pk}-{Message.objects.count()}",
    )


def section_url() -> str:
    return reverse("section", args=["estadisticas"]) + "?view=temas-conversacion"


class TokenizeTests(TestCase):
    def test_greetings_and_stopwords_never_become_topics(self):
        self.assertEqual(list(temas.tokenize("Hola, buenos días señor!! Cómo está")), [])

    def test_short_tokens_and_digits_are_dropped(self):
        self.assertEqual(list(temas.tokenize("el iva es 19% ya")), [("iva", "iva")])

    def test_accents_fold_into_one_key_but_raw_form_is_kept(self):
        self.assertEqual(
            list(temas.tokenize("envío envio")), [("envio", "envío"), ("envio", "envio")]
        )

    def test_enye_is_not_folded(self):
        self.assertEqual(list(temas.tokenize("diseño")), [("diseño", "diseño")])


class TemasReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.chat_a = make_conversation("+573000000201")
        self.chat_b = make_conversation("+573000000202")

    def report(self, period_key=temas.DEFAULT_PERIOD):
        return temas.report(temas.PERIOD_BY_KEY[period_key])

    def test_ranks_by_conversations_then_mentions(self):
        say(self.chat_a, "el precio del envío")          # precio, envío
        say(self.chat_b, "precio precio precio")          # precio x3, one chat
        say(self.chat_b, "quiero saber el envío")         # envío -- 2 chats
        report = self.report()
        # Both reach 2 conversations; mentions break the tie.
        self.assertEqual(
            [(t.word, t.conversations, t.mentions) for t in report["topics"][:2]],
            [("precio", 2, 4), ("envío", 2, 2)],
        )
        self.assertEqual(report["max_conversations"], 2)

    def test_accented_and_plain_spellings_count_as_one_topic(self):
        say(self.chat_a, "cotización")
        say(self.chat_b, "cotización y cotizacion")
        report = self.report()
        topic = report["topics"][0]
        # Most common raw spelling wins the display form.
        self.assertEqual(topic.word, "cotización")
        self.assertEqual(topic.mentions, 3)
        self.assertEqual(topic.conversations, 2)

    def test_outbound_messages_are_not_analyzed(self):
        say(self.chat_a, "garantía")
        say(self.chat_a, "factura factura", direction=Message.OUTBOUND)
        report = self.report()
        self.assertEqual([t.word for t in report["topics"]], ["garantía"])
        self.assertEqual(report["analyzed_messages"], 1)

    def test_period_window_excludes_old_messages(self):
        say(self.chat_a, "reserva", days_ago=40)
        say(self.chat_b, "domicilio", days_ago=1)
        month = self.report("30")
        self.assertEqual([t.word for t in month["topics"]], ["domicilio"])
        everything = self.report("todo")
        self.assertEqual(
            {t.word for t in everything["topics"]}, {"reserva", "domicilio"}
        )

    def test_table_is_capped_but_total_counts_everything(self):
        words = [f"palabra{chr(97 + i)}" for i in range(temas.TOP_N + 3)]
        say(self.chat_a, " ".join(words))
        report = self.report()
        self.assertEqual(len(report["topics"]), temas.TOP_N)
        self.assertEqual(report["total_topics"], temas.TOP_N + 3)

    def test_empty_period_reports_zeros_without_crashing(self):
        report = self.report()
        self.assertEqual(report["topics"], [])
        self.assertEqual(report["max_conversations"], 0)
        self.assertEqual(report["analyzed_messages"], 0)
        self.assertEqual(report["conversation_count"], 0)


class TemasPanelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.chat = make_conversation("+573000000210")
        say(self.chat, "necesito el precio del envío urgente")

    def test_panel_is_real_not_the_placeholder(self):
        response = self.client.get(section_url())
        self.assertEqual(response.context["active_view"], "temas-conversacion")
        self.assertEqual(
            response.context["panel_template"],
            "partials/estadisticas/panels/temas-conversacion.html",
        )
        self.assertContains(response, "Temas de conversación")
        self.assertNotContains(response, "próximamente")

    def test_topics_render_with_counts_and_bars(self):
        html = self.client.get(section_url()).content.decode()
        for word in ["precio", "envío", "urgente"]:
            with self.subTest(word):
                self.assertIn(f'table__name">{word}</td>', html)
        self.assertIn("width: 100%", html)

    def test_period_select_offers_every_option_with_the_default_selected(self):
        html = self.client.get(section_url()).content.decode()
        for option in temas.PERIODS:
            with self.subTest(option.key):
                self.assertIn(f'value="{option.key}"', html)
        self.assertIn(f'value="{temas.DEFAULT_PERIOD}" selected', html)

    def test_unknown_period_falls_back_to_the_default(self):
        response = self.client.get(section_url() + "&period=bogus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"].key, temas.DEFAULT_PERIOD)

    def test_panel_endpoint_honors_the_period_param(self):
        old_chat = make_conversation("+573000000211")
        say(old_chat, "membresía", days_ago=60)
        response = self.client.get(
            reverse("estadisticas_panel", args=["temas-conversacion"]),
            {"period": "todo"},
        )
        body = response.content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)
        self.assertIn("membresía", body)
        self.assertIn('value="todo" selected', body)

    def test_no_messages_renders_the_empty_state_with_the_filter(self):
        Message.objects.all().delete()
        cache.clear()
        response = self.client.get(section_url())
        self.assertContains(response, "Aún no hay temas que mostrar")
        # The filter stays available so a wider period is one click away.
        self.assertContains(response, 'name="period"')
