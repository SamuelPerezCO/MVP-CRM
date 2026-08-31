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


class QuickRepliesEndpointTests(TestCase):
    def get(self):
        return self.client.get(reverse("inbox_quick_replies"))

    def test_lists_approved_active_templates_with_rendered_bodies(self):
        template(name="saludo_inicial", samples=["Camila"])
        html = self.get().content.decode()
        self.assertIn("saludo_inicial", html)
        self.assertIn("Hola Camila", html)
        # The picker carries the body in the attribute shell.js reads.
        self.assertIn("data-quick-body", html)

    def test_pending_rejected_and_inactive_templates_are_not_offered(self):
        template(name="pendiente_aun", status="pendiente")
        template(name="rechazada_ya", status="rechazada")
        template(name="apagada", is_active=False)
        html = self.get().content.decode()
        for name in ["pendiente_aun", "rechazada_ya", "apagada"]:
            with self.subTest(name):
                self.assertNotIn(name, html)

    def test_empty_state_links_to_the_plantillas_section(self):
        html = self.get().content.decode()
        self.assertIn("No hay plantillas aprobadas", html)
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
        self.assertIn(reverse("inbox_quick_replies"), html)
        self.assertIn('hx-trigger="toggle once"', html)
