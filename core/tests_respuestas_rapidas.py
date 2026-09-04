"""Tests for core.plantillas.render_body (what the Enviar plantilla flow
sends) and the composer's Respuestas rápidas picker hookup."""

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


class ComposerHookupTests(TestCase):
    """The picker's wiring in the composer. What it lists and sends is
    covered in core.tests_respuestas (it lists QuickReply rows now, not
    plantillas -- those belong to the Enviar plantilla flow)."""

    def setUp(self):
        self.conversation = conversation()

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

    def test_plantillas_no_longer_appear_in_the_picker(self):
        template(name="saludo_inicial", samples=["Camila"])
        html = self.client.get(
            reverse("inbox_quick_replies", args=[self.conversation.pk])
        ).content.decode()
        self.assertNotIn("saludo_inicial", html)

    def test_an_unknown_conversation_is_404(self):
        self.assertEqual(
            self.client.get(reverse("inbox_quick_replies", args=[999999])).status_code,
            404,
        )
