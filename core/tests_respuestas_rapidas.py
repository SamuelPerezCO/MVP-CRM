"""Tests for the composer's Respuestas rápidas picker: the body rendering in
core.plantillas.render_body, the popover endpoint, and the composer hookup."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import plantillas
from core.models import Client, MessageTemplate
from messaging.models import Conversation


def template(name="saludo_inicial", body="Hola {{1}}, ¿en qué te ayudo?",
             samples=None, status="aceptada", is_active=True, language="es"):
    return MessageTemplate.objects.create(
        name=name,
        body=body,
        body_sample_values=["Camila"] if samples is None else samples,
        status=status,
        is_active=is_active,
        language=language,
    )


class RenderBodyTests(TestCase):
    def test_samples_substitute_their_variables(self):
        entry = template(body="Hola {{1}}, tu pedido {{2}} va en camino.",
                         samples=["Camila", "#4512"])
        self.assertEqual(
            plantillas.render_body(entry),
            "Hola Camila, tu pedido #4512 va en camino.",
        )

    def test_a_variable_without_a_sample_keeps_its_placeholder(self):
        # The agent should SEE the blank, not silently send a hole.
        entry = template(body="Hola {{1}}, código: {{2}}", samples=["Camila"])
        self.assertEqual(plantillas.render_body(entry), "Hola Camila, código: {{2}}")

    def test_an_empty_sample_also_keeps_the_placeholder(self):
        entry = template(body="Hola {{1}}", samples=[""])
        self.assertEqual(plantillas.render_body(entry), "Hola {{1}}")

    def test_a_repeated_variable_substitutes_everywhere(self):
        entry = template(body="{{1}}, sí, {{1}}", samples=["Camila"])
        self.assertEqual(plantillas.render_body(entry), "Camila, sí, Camila")

    def test_a_body_without_variables_passes_through(self):
        entry = template(body="Gracias por escribirnos.", samples=[])
        self.assertEqual(plantillas.render_body(entry), "Gracias por escribirnos.")


def conversation(phone="+573000000777"):
    """An open conversation -- the picker is scoped to one, since every
    entry posts itself into that thread."""
    contact = Client.objects.create(first_name="Camila", last_name="Test", phone=phone)
    return Conversation.objects.create(
        contact=contact,
        channel="whatsapp",
        last_inbound_at=timezone.now() - timedelta(hours=1),
    )


class QuickRepliesEndpointTests(TestCase):
    def setUp(self):
        self.conversation = conversation()

    def get(self):
        return self.client.get(
            reverse("inbox_quick_replies", args=[self.conversation.pk])
        )

    def test_lists_approved_active_templates_with_rendered_bodies(self):
        template(name="saludo_inicial", samples=["Camila"])
        html = self.get().content.decode()
        self.assertIn("saludo_inicial", html)
        self.assertIn("Hola Camila", html)

    def test_a_sendable_entry_posts_itself_into_the_open_chat(self):
        # The whole point of a quick reply: one click puts it in the thread.
        template(name="saludo_inicial", samples=["Camila"])
        html = self.get().content.decode()
        self.assertIn(
            f'hx-post="{reverse("inbox_send", args=[self.conversation.pk])}"', html
        )
        self.assertIn('hx-target="#chat-messages"', html)
        self.assertIn("data-quick-send", html)
        # The body travels as JSON in hx-vals, entity-escaped for the attribute.
        self.assertIn("&quot;Hola Camila, ¿en qué te ayudo?&quot;", html)

    def test_an_entry_with_a_blank_left_loads_the_composer_instead(self):
        # Sending it would put a literal "{{2}}" in front of the customer.
        template(name="con_hueco", body="Hola {{1}}, código {{2}}", samples=["Camila"])
        html = self.get().content.decode()
        self.assertIn("data-quick-body", html)
        self.assertNotIn("data-quick-send", html)
        self.assertIn("Completar", html)

    def test_an_unknown_conversation_is_404(self):
        self.assertEqual(
            self.client.get(reverse("inbox_quick_replies", args=[999999])).status_code,
            404,
        )

    def test_rejected_and_inactive_templates_are_not_offered(self):
        template(name="rechazada_ya", status="rechazada")
        template(name="apagada", is_active=False)
        html = self.get().content.decode()
        for name in ["rechazada_ya", "apagada"]:
            with self.subTest(name):
                self.assertNotIn(name, html)

    def test_a_freshly_created_template_shows_up_flagged_pendiente(self):
        # The editor saves with status "pendiente" and the MVP has no real
        # Meta approval pipeline -- excluding pendientes would make every
        # user-created plantilla invisible in the picker forever.
        template(name="recien_creada", status="pendiente")
        html = self.get().content.decode()
        self.assertIn("recien_creada", html)
        self.assertIn("Pendiente", html)

    def test_an_accepted_sendable_template_carries_no_badge(self):
        template(name="ya_aprobada", status="aceptada")
        html = self.get().content.decode()
        self.assertIn("ya_aprobada", html)
        self.assertNotIn("quickreplies__badge", html)

    def test_empty_state_links_to_the_plantillas_section(self):
        html = self.get().content.decode()
        self.assertIn("No hay plantillas activas", html)
        self.assertIn(reverse("section", args=["mensajeria"]), html)

    def test_templates_come_out_in_name_order(self):
        template(name="zz_despedida")
        template(name="aa_saludo")
        html = self.get().content.decode()
        self.assertLess(html.index("aa_saludo"), html.index("zz_despedida"))


class ComposerHookupTests(TestCase):
    def setUp(self):
        contact = Client.objects.create(
            first_name="Camila", last_name="Test", phone="+573000000777"
        )
        # A recent inbound keeps the 24h window open, so the composer (and
        # with it the picker) actually renders.
        self.conversation = Conversation.objects.create(
            contact=contact,
            channel="whatsapp",
            last_inbound_at=timezone.now() - timedelta(hours=1),
        )

    def test_the_picker_loads_lazily_from_its_endpoint(self):
        response = self.client.get(
            reverse("inbox_chat", args=[self.conversation.pk])
        )
        html = response.content.decode()
        self.assertIn("data-quickreplies", html)
        self.assertIn(
            reverse("inbox_quick_replies", args=[self.conversation.pk]), html
        )
        self.assertIn('hx-trigger="toggle once"', html)

    def test_the_picker_carries_the_csrf_token_its_entries_post_with(self):
        # The entries are buttons, not this form's submit, so they inherit
        # the token from hx-headers on the <details> rather than {% csrf_token %}.
        html = self.client.get(
            reverse("inbox_chat", args=[self.conversation.pk])
        ).content.decode()
        self.assertIn("X-CSRFToken", html)

    def test_picking_a_quick_reply_lands_the_message_in_the_thread(self):
        # End to end over the same route the popover button posts to.
        response = self.client.post(
            reverse("inbox_send", args=[self.conversation.pk]),
            {"body": "Hola Camila, ¿en qué te ayudo?"},
        )
        self.assertContains(response, "Hola Camila, ¿en qué te ayudo?")
        self.assertEqual(self.conversation.messages.count(), 1)
