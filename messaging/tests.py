"""Tests for the messaging layer: webhook contract, 24h rule, Inbox wiring."""

from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Client

from unittest.mock import patch

from . import services
from .models import Conversation, ConversationTag, Message, Tag
from .providers.baileys import BaileysProvider
from .providers.registry import get_provider
from .providers.types import InboundEvent, MessageStatus
from .services import SendWindowClosed, send_message

WEBHOOK_URL = "/webhooks/messaging/fake/"
GOOD_SIGNATURE = {"X-Fake-Signature": settings.MESSAGING_FAKE_SECRET}


def webhook_payload(events: list[dict]) -> str:
    return json.dumps({"events": events})


def message_event(**overrides) -> dict:
    event = {
        "event_type": "message",
        "provider_message_id": "prov-msg-1",
        "from_number": "+573000000099",
        "body": "Hola, ¿tienen envíos a Cali?",
        "channel": "whatsapp",
        "contact_name": "Cliente Prueba",
    }
    event.update(overrides)
    return event


class WebhookTests(TestCase):
    def post_webhook(self, payload: str, headers=GOOD_SIGNATURE):
        return self.client.post(
            WEBHOOK_URL, data=payload, content_type="application/json", headers=headers
        )

    def test_inbound_message_creates_contact_conversation_and_message(self):
        response = self.post_webhook(webhook_payload([message_event()]))

        self.assertEqual(response.status_code, 200)
        contact = Client.objects.get(phone="+573000000099")
        self.assertEqual(contact.first_name, "Cliente Prueba")
        conversation = contact.conversations.get()
        self.assertEqual(conversation.unread_count, 1)
        self.assertIsNotNone(conversation.last_inbound_at)
        message = conversation.messages.get()
        self.assertEqual(message.direction, Message.INBOUND)
        self.assertEqual(message.provider_message_id, "prov-msg-1")

    def test_same_provider_message_id_twice_creates_one_message(self):
        """Providers retry deliveries; a retry must be a no-op."""
        payload = webhook_payload([message_event()])
        self.assertEqual(self.post_webhook(payload).status_code, 200)
        self.assertEqual(self.post_webhook(payload).status_code, 200)

        self.assertEqual(Message.objects.count(), 1)
        # The duplicate must not bump counters either.
        self.assertEqual(Conversation.objects.get().unread_count, 1)

    def test_bad_signature_rejected_with_401_and_nothing_created(self):
        response = self.post_webhook(
            webhook_payload([message_event()]), headers={"X-Fake-Signature": "forged"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)

    def test_missing_signature_rejected_with_401(self):
        response = self.client.post(
            WEBHOOK_URL,
            data=webhook_payload([message_event()]),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_unparseable_payload_still_answers_200(self):
        """Non-200 would trigger a provider retry storm over the same junk."""
        response = self.post_webhook("this is not json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.count(), 0)

    def test_unknown_provider_404s(self):
        response = self.client.post("/webhooks/messaging/nope/", data="{}",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 404)

    def test_get_handshake_echoes_challenge(self):
        response = self.client.get(WEBHOOK_URL, {"hub.challenge": "12345"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "12345")

    def test_second_message_reuses_open_conversation(self):
        self.post_webhook(webhook_payload([message_event()]))
        self.post_webhook(
            webhook_payload([message_event(provider_message_id="prov-msg-2",
                                           body="¿Y a Medellín?")])
        )
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Conversation.objects.get().unread_count, 2)

    def test_inbound_reopens_resolved_conversation_as_new_thread(self):
        self.post_webhook(webhook_payload([message_event()]))
        conversation = Conversation.objects.get()
        conversation.status = Conversation.RESOLVED
        conversation.save()

        self.post_webhook(
            webhook_payload([message_event(provider_message_id="prov-msg-2")])
        )
        # Resolved threads are history: the new inbound starts a fresh one.
        self.assertEqual(Conversation.objects.count(), 2)


class StatusEventTests(TestCase):
    def setUp(self):
        contact = Client.objects.create(first_name="Ana", phone="+573000000098")
        self.conversation = Conversation.objects.create(contact=contact)
        self.message = Message.objects.create(
            conversation=self.conversation,
            direction=Message.OUTBOUND,
            body="Hola",
            status=MessageStatus.SENT.value,
            provider_message_id="out-1",
        )

    def post_status(self, status: str):
        payload = webhook_payload(
            [{"event_type": "status", "provider_message_id": "out-1", "status": status}]
        )
        return self.client.post(
            "/webhooks/messaging/fake/", data=payload,
            content_type="application/json", headers=GOOD_SIGNATURE,
        )

    def test_status_moves_forward(self):
        self.post_status("delivered")
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "delivered")

    def test_status_never_moves_backward(self):
        """Receipts arrive out of order; 'read' must not regress."""
        self.post_status("read")
        self.post_status("delivered")  # late retry of an older receipt
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, "read")


class SendWindowTests(TestCase):
    def setUp(self):
        contact = Client.objects.create(first_name="Luis", phone="+573000000097")
        self.conversation = Conversation.objects.create(contact=contact)

    def test_send_outside_24h_window_raises_and_creates_nothing(self):
        self.conversation.last_inbound_at = timezone.now() - timedelta(hours=25)
        self.conversation.save()

        with self.assertRaises(SendWindowClosed):
            send_message(self.conversation, "Hola de nuevo")
        self.assertEqual(Message.objects.count(), 0)

    def test_send_with_no_inbound_ever_raises(self):
        """A thread the customer never wrote to has no window at all."""
        with self.assertRaises(SendWindowClosed):
            send_message(self.conversation, "Hola")

    def test_send_inside_window_succeeds(self):
        self.conversation.last_inbound_at = timezone.now() - timedelta(hours=1)
        self.conversation.save()

        message = send_message(self.conversation, "¡Claro que sí!")

        self.assertEqual(message.direction, Message.OUTBOUND)
        self.assertTrue(message.provider_message_id.startswith("fake-"))
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.last_message_at, message.timestamp)


class FakeStatusProgressionTests(TestCase):
    def test_fake_provider_advances_outbound_statuses_one_step(self):
        contact = Client.objects.create(first_name="Sara", phone="+573000000096")
        conversation = Conversation.objects.create(
            contact=contact, last_inbound_at=timezone.now()
        )
        message = send_message(conversation, "Hola Sara")
        # Age the message past every delay: still only one step per pump.
        Message.objects.filter(pk=message.pk).update(
            timestamp=timezone.now() - timedelta(seconds=60)
        )

        provider = get_provider("fake")
        events = provider.pending_status_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, MessageStatus.SENT)


class InboxUITests(TestCase):
    """The messaging data showing up through the existing Inbox screen."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("asesor", password="x")
        self.contact = Client.objects.create(
            first_name="Camila", last_name="Pruebas", phone="+573000000095",
            channel="whatsapp",
        )
        now = timezone.now()
        self.conversation = Conversation.objects.create(
            contact=self.contact, channel="whatsapp", assigned_to=self.user,
            last_message_at=now, last_inbound_at=now, unread_count=3,
        )
        Message.objects.create(
            conversation=self.conversation, direction=Message.INBOUND,
            body="Hola, ¿tienen tallas grandes?", provider_message_id="ui-1",
            status="delivered", timestamp=now,
        )

    def test_conversation_list_shows_real_rows(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertContains(response, "Camila Pruebas")
        self.assertContains(response, "asesor")        # line 2: assigned agent
        self.assertContains(response, "conv-row__dot")  # unread indicator
        self.assertNotContains(response, "Tu inbox está vacío")

    def test_channel_counts_reflect_unread_conversations(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        counts = response.context["counts"]
        self.assertEqual(counts["whatsapp"], 1)
        self.assertEqual(counts["messenger"], 0)

    def test_tu_inbox_filter_needs_the_assigned_user(self):
        anonymous = self.client.get(reverse("inbox_list", args=["tu-inbox"]))
        self.assertContains(anonymous, "Tu inbox está vacío")

        self.client.force_login(self.user)
        assigned = self.client.get(reverse("inbox_list", args=["tu-inbox"]))
        self.assertContains(assigned, "Camila Pruebas")

    def test_sin_asignar_filter_shows_only_unassigned(self):
        response = self.client.get(reverse("inbox_list", args=["sin-asignar"]))
        self.assertContains(response, "Tu inbox está vacío")

        self.conversation.assigned_to = None
        self.conversation.save()
        response = self.client.get(reverse("inbox_list", args=["sin-asignar"]))
        self.assertContains(response, "Camila Pruebas")

    def test_opening_a_chat_renders_thread_and_clears_unread(self):
        response = self.client.get(reverse("inbox_chat", args=[self.conversation.pk]))
        self.assertContains(response, "tallas grandes")
        self.assertContains(response, "composer")          # window open -> live form
        self.assertContains(response, 'id="details-panel"')  # out-of-band column 5
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.unread_count, 0)

    def test_closed_window_disables_composer_with_notice(self):
        Conversation.objects.filter(pk=self.conversation.pk).update(
            last_inbound_at=timezone.now() - timedelta(hours=30)
        )
        response = self.client.get(reverse("inbox_chat", args=[self.conversation.pk]))
        self.assertContains(response, "ventana de 24 horas")
        self.assertNotContains(response, "composer__input")

    def test_sending_from_the_composer_creates_an_outbound_message(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("inbox_send", args=[self.conversation.pk]),
            {"body": "¡Sí! Hasta talla XXL."},
        )
        self.assertEqual(response.status_code, 200)
        outbound = self.conversation.messages.get(direction=Message.OUTBOUND)
        self.assertEqual(outbound.sent_by, self.user)
        self.assertTrue(outbound.provider_message_id.startswith("fake-"))


# --- Tags -------------------------------------------------------------------


def make_conversation(phone: str, channel: str = "whatsapp") -> Conversation:
    contact = Client.objects.create(first_name=f"C{phone[-2:]}", phone=phone)
    return Conversation.objects.create(
        contact=contact, channel=channel, last_message_at=timezone.now()
    )


class TagServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("agente", password="x")
        self.conversation = make_conversation("+573000000090")

    def test_tag_names_are_unique_case_insensitively(self):
        services.create_tag("Cliente Nuevo", "yellow", self.user)
        with self.assertRaises(services.TagNameTaken):
            services.create_tag("CLIENTE NUEVO", "green", self.user)
        self.assertEqual(Tag.objects.count(), 1)

    def test_create_tag_validates_name_and_color(self):
        with self.assertRaises(ValueError):
            services.create_tag("   ", "green")
        with self.assertRaises(ValueError):
            services.create_tag("Ventas", "#ff0000")  # raw hex is not a token

    def test_apply_tag_is_idempotent_and_audited(self):
        tag = services.create_tag("VIP", "purple", self.user)
        services.apply_tag([self.conversation], tag, self.user)
        services.apply_tag([self.conversation], tag, self.user)  # retry/no-op

        link = ConversationTag.objects.get()  # exactly one despite two calls
        self.assertEqual(link.tagged_by, self.user)
        self.assertIsNotNone(link.tagged_at)

    def test_archived_tag_cannot_be_applied_but_keeps_history(self):
        tag = services.create_tag("HISTÓRICA", "gray", self.user)
        services.apply_tag([self.conversation], tag, self.user)

        services.set_tag_archived(tag, True)

        # The 500-chats rule: archiving must not rewrite the past.
        self.assertEqual(ConversationTag.objects.count(), 1)
        with self.assertRaises(ValueError):
            services.apply_tag([make_conversation("+573000000091")], tag)

    def test_update_tag_renames_and_recolors(self):
        tag = services.create_tag("MAYORISTA", "blue", self.user)
        services.update_tag(tag, "MAYORISTA EFECTIVA", "green")
        tag.refresh_from_db()
        self.assertEqual((tag.name, tag.color), ("MAYORISTA EFECTIVA", "green"))

    def test_bulk_apply_and_remove(self):
        tag = services.create_tag("PROMO", "orange", self.user)
        conversations = [make_conversation(f"+57300000009{i}") for i in (2, 3, 4)]

        self.assertEqual(services.apply_tag(conversations, tag, self.user), 3)
        self.assertEqual(services.remove_tag(conversations[:2], tag), 2)
        self.assertEqual(ConversationTag.objects.count(), 1)


class TagFilterTests(TestCase):
    """AND semantics, composition with the nav filters, and flat query counts."""

    def setUp(self):
        self.tag_a = services.create_tag("A", "green")
        self.tag_b = services.create_tag("B", "blue")
        self.both = make_conversation("+573000000080")
        self.only_a = make_conversation("+573000000081", channel="messenger")
        self.untagged = make_conversation("+573000000082")
        services.apply_tag([self.both, self.only_a], self.tag_a)
        services.apply_tag([self.both], self.tag_b)

    def list_response(self, filter_key="todos", tags=()):
        return self.client.get(
            reverse("inbox_list", args=[filter_key]), {"tags": [t.pk for t in tags]}
        )

    def test_single_tag_narrows_the_list(self):
        html = self.list_response(tags=[self.tag_a]).content.decode()
        self.assertIn(self.both.contact.full_name, html)
        self.assertIn(self.only_a.contact.full_name, html)
        self.assertNotIn(self.untagged.contact.full_name, html)

    def test_multiple_tags_mean_and_not_or(self):
        html = self.list_response(tags=[self.tag_a, self.tag_b]).content.decode()
        self.assertIn(self.both.contact.full_name, html)
        self.assertNotIn(self.only_a.contact.full_name, html)  # has A but not B

    def test_tag_filter_composes_with_channel_filter(self):
        html = self.list_response("messenger", tags=[self.tag_a]).content.decode()
        self.assertIn(self.only_a.contact.full_name, html)
        self.assertNotIn(self.both.contact.full_name, html)  # tagged, wrong channel

    def test_query_count_stays_flat_as_the_list_grows(self):
        """Tags are prefetched, so more rows must not mean more queries."""
        url = reverse("inbox_list", args=["todos"])

        with CaptureQueriesContext(connection) as small:
            self.client.get(url)

        for i in range(10):  # 10 more conversations, all tagged
            conversation = make_conversation(f"+5730000001{i:02d}")
            services.apply_tag([conversation], self.tag_a)

        with CaptureQueriesContext(connection) as large:
            self.client.get(url)

        self.assertEqual(len(small), len(large))


class TagAdminUITests(TestCase):
    """The Etiquetas page (CRM > Gestión de clientes) and its endpoints."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("admin", password="x")
        self.client.force_login(self.user)

    def test_panel_renders_tags_as_pills_with_usage(self):
        tag = services.create_tag("VENTA EFECTIVA", "green", self.user)
        services.apply_tag([make_conversation("+573000000070")], tag)

        response = self.client.get(
            reverse("section", args=["crm"]), {"view": "etiquetas"}
        )
        self.assertContains(response, "tag-pill--green")
        self.assertContains(response, "VENTA EFECTIVA")
        self.assertContains(response, "+ Crear etiqueta")
        self.assertEqual(response.context["tags"][0].usage, 1)

    def test_create_endpoint_creates_and_rerenders_table(self):
        response = self.client.post(
            reverse("tag_create"), {"name": "SHOPIFY NUEVO", "color": "purple"}
        )
        self.assertContains(response, "SHOPIFY NUEVO")
        tag = Tag.objects.get()
        self.assertEqual((tag.color, tag.created_by), ("purple", self.user))

    def test_duplicate_name_shows_error_not_a_second_tag(self):
        services.create_tag("VIP", "purple")
        response = self.client.post(
            reverse("tag_create"), {"name": "vip", "color": "green"}
        )
        self.assertContains(response, "Ya existe una etiqueta")
        self.assertEqual(Tag.objects.count(), 1)

    def test_edit_updates_every_pill_at_once(self):
        tag = services.create_tag("MAYORISTA", "blue")
        conversation = make_conversation("+573000000071")
        services.apply_tag([conversation], tag)

        self.client.post(
            reverse("tag_update", args=[tag.pk]),
            {"name": "MAYORISTA EFECTIVA", "color": "green"},
        )

        # The conversation list shows the new name/color without retagging --
        # the rows reference the Tag row, not a copied string.
        html = self.client.get(reverse("inbox_list", args=["todos"])).content.decode()
        self.assertIn("MAYORISTA EFECTIVA", html)
        self.assertIn("tag-pill--green", html)

    def test_archive_hides_from_pickers_but_not_from_rows(self):
        tag = services.create_tag("VIEJA", "gray")
        conversation = make_conversation("+573000000072")
        services.apply_tag([conversation], tag)

        self.client.post(reverse("tag_archive", args=[tag.pk]), {"archived": "1"})

        # Gone from the picker...
        picker = self.client.get(
            reverse("conversation_tags", args=[conversation.pk])
        ).content.decode()
        self.assertNotIn("VIEJA", picker)
        # ...but still on the tagged row, and the link rows survive.
        rows = self.client.get(reverse("inbox_list", args=["todos"])).content.decode()
        self.assertIn("VIEJA", rows)
        self.assertEqual(ConversationTag.objects.count(), 1)


class TagPickerTests(TestCase):
    """The per-conversation picker shared by the rows and the chat header."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("agente", password="x")
        self.client.force_login(self.user)
        self.conversation = make_conversation("+573000000060")
        self.tag = services.create_tag("PROMO", "orange")

    def picker_url(self):
        return reverse("conversation_tags", args=[self.conversation.pk])

    def test_get_lists_tags_and_marks_applied_ones(self):
        services.apply_tag([self.conversation], self.tag)
        response = self.client.get(self.picker_url())
        self.assertContains(response, "PROMO")
        self.assertContains(response, "checked")

    def test_toggle_add_then_remove(self):
        self.client.post(self.picker_url(), {"tag_id": self.tag.pk, "action": "add"})
        self.assertEqual(self.conversation.tags.count(), 1)
        self.assertEqual(ConversationTag.objects.get().tagged_by, self.user)

        self.client.post(self.picker_url(), {"tag_id": self.tag.pk, "action": "remove"})
        self.assertEqual(self.conversation.tags.count(), 0)

    def test_toggle_response_updates_pills_out_of_band(self):
        response = self.client.post(
            self.picker_url(), {"tag_id": self.tag.pk, "action": "add"}
        )
        # All three pill surfaces: list row, chat header, details panel.
        self.assertContains(response, f'id="conv-tags-{self.conversation.pk}"')
        self.assertContains(response, f'id="chat-tags-{self.conversation.pk}"')
        self.assertContains(response, f'id="details-tags-{self.conversation.pk}"')
        self.assertContains(response, "hx-swap-oob")

    def test_details_panel_has_the_tag_editor(self):
        services.apply_tag([self.conversation], self.tag)
        response = self.client.get(reverse("inbox_chat", args=[self.conversation.pk]))
        self.assertContains(response, "+ Añadir etiqueta")
        self.assertContains(response, f'id="details-tags-{self.conversation.pk}"')
        self.assertContains(response, "tag-pill__remove")  # the × on each pill

    def test_conversation_list_has_no_picker(self):
        """Tags in the list are display-only; editing lives in column 5."""
        html = self.client.get(reverse("inbox_list", args=["todos"])).content.decode()
        self.assertNotIn("tag-picker", html)
        self.assertNotIn("Añadir etiqueta", html)

    def test_inline_create_invents_a_tag_without_leaving_the_inbox(self):
        response = self.client.post(self.picker_url(), {"new_name": "URGENTE"})
        self.assertEqual(response.status_code, 200)
        tag = Tag.objects.get(name="URGENTE")
        self.assertIn(tag, self.conversation.tags.all())
        self.assertEqual(tag.created_by, self.user)

    def test_search_offers_create_only_when_nothing_matches(self):
        no_match = self.client.get(self.picker_url(), {"q": "MAYORISTA"})
        self.assertContains(no_match, "Crear «MAYORISTA»")
        match = self.client.get(self.picker_url(), {"q": "PROMO"})
        self.assertNotContains(match, "Crear «")


class BulkTagTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("agente", password="x")
        self.client.force_login(self.user)
        self.tag = services.create_tag("PROMO", "orange")
        self.conversations = [make_conversation(f"+5730000000{i}") for i in (50, 51, 52)]

    def test_bulk_add_tags_every_selected_conversation(self):
        response = self.client.post(
            reverse("inbox_tags_bulk"),
            {
                "tag_id": self.tag.pk,
                "action": "add",
                "selected": [c.pk for c in self.conversations[:2]],
                "filter": "todos",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ConversationTag.objects.count(), 2)
        # The response is the refreshed list, pills included.
        self.assertContains(response, "PROMO")

    def test_bulk_remove(self):
        services.apply_tag(self.conversations, self.tag)
        self.client.post(
            reverse("inbox_tags_bulk"),
            {
                "tag_id": self.tag.pk,
                "action": "remove",
                "selected": [c.pk for c in self.conversations],
                "filter": "todos",
            },
        )
        self.assertEqual(ConversationTag.objects.count(), 0)


class BaileysProviderTests(TestCase):
    """The baileys provider: sidecar HTTP calls and webhook parsing."""

    def setUp(self):
        self.provider = BaileysProvider()

    # --- verify_signature ---------------------------------------------

    def test_verify_signature_accepts_matching_secret(self):
        request = self.client.post(
            "/webhooks/messaging/baileys/",
            data="{}",
            content_type="application/json",
            headers={"X-Sidecar-Secret": settings.BAILEYS_SIDECAR_SECRET},
        ).wsgi_request
        self.assertTrue(self.provider.verify_signature(request))

    def test_verify_signature_rejects_wrong_secret(self):
        request = self.client.post(
            "/webhooks/messaging/baileys/",
            data="{}",
            content_type="application/json",
            headers={"X-Sidecar-Secret": "not-the-secret"},
        ).wsgi_request
        self.assertFalse(self.provider.verify_signature(request))

    def test_verify_signature_rejects_missing_header(self):
        request = self.client.post(
            "/webhooks/messaging/baileys/", data="{}", content_type="application/json"
        ).wsgi_request
        self.assertFalse(self.provider.verify_signature(request))

    # --- parse_webhook ---------------------------------------------------

    def test_parse_webhook_message_event(self):
        payload = json.dumps(
            {
                "events": [
                    {
                        "event_type": "message",
                        "provider_message_id": "3EB0ABCDEF",
                        "from_number": "+573000000099",
                        "body": "Hola desde WhatsApp real",
                        "channel": "whatsapp",
                        "contact_name": "Cliente Real",
                        "timestamp": "2026-01-15T10:00:00+00:00",
                    }
                ]
            }
        )
        request = self.client.post(
            "/webhooks/messaging/baileys/", data=payload, content_type="application/json"
        ).wsgi_request
        events = self.provider.parse_webhook(request)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, InboundEvent)
        self.assertEqual(event.event_type, "message")
        self.assertEqual(event.provider_message_id, "3EB0ABCDEF")
        self.assertEqual(event.from_number, "+573000000099")
        self.assertEqual(event.body, "Hola desde WhatsApp real")
        self.assertEqual(event.contact_name, "Cliente Real")

    def test_parse_webhook_status_event(self):
        payload = json.dumps(
            {
                "events": [
                    {
                        "event_type": "status",
                        "provider_message_id": "3EB0ABCDEF",
                        "status": "delivered",
                    }
                ]
            }
        )
        request = self.client.post(
            "/webhooks/messaging/baileys/", data=payload, content_type="application/json"
        ).wsgi_request
        events = self.provider.parse_webhook(request)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "status")
        self.assertEqual(events[0].status, MessageStatus.DELIVERED)

    def test_parse_webhook_rejects_unparseable_payload(self):
        request = self.client.post(
            "/webhooks/messaging/baileys/", data="not json", content_type="application/json"
        ).wsgi_request
        with self.assertRaises(ValueError):
            self.provider.parse_webhook(request)

    def test_parse_webhook_end_to_end_via_endpoint(self):
        """The full webhook path -- signature, parse, process -- like the
        fake-provider WebhookTests, but through the baileys slug."""
        payload = json.dumps(
            {
                "events": [
                    {
                        "event_type": "message",
                        "provider_message_id": "3EB0REAL1",
                        "from_number": "+573000000098",
                        "body": "Buenas, ¿tienen stock?",
                        "contact_name": "Nuevo Cliente",
                    }
                ]
            }
        )
        response = self.client.post(
            "/webhooks/messaging/baileys/",
            data=payload,
            content_type="application/json",
            headers={"X-Sidecar-Secret": settings.BAILEYS_SIDECAR_SECRET},
        )
        self.assertEqual(response.status_code, 200)
        contact = Client.objects.get(phone="+573000000098")
        self.assertEqual(contact.first_name, "Nuevo Cliente")
        self.assertEqual(contact.conversations.get().messages.get().body, "Buenas, ¿tienen stock?")

    def test_webhook_endpoint_rejects_bad_signature(self):
        response = self.client.post(
            "/webhooks/messaging/baileys/",
            data=json.dumps({"events": []}),
            content_type="application/json",
            headers={"X-Sidecar-Secret": "wrong"},
        )
        self.assertEqual(response.status_code, 401)

    # --- sending (HTTP call to the sidecar, mocked) -----------------------

    @patch("messaging.providers.baileys.requests.post")
    def test_send_text_posts_to_sidecar_and_returns_id(self, mock_post):
        mock_post.return_value.json.return_value = {"id": "3EB0SENT1"}
        mock_post.return_value.raise_for_status.return_value = None

        message_id = self.provider.send_text("+573000000097", "Hola!")

        self.assertEqual(message_id, "3EB0SENT1")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"], {"to": "+573000000097", "body": "Hola!"})
        self.assertEqual(
            kwargs["headers"]["X-Sidecar-Secret"], settings.BAILEYS_SIDECAR_SECRET
        )

    @patch("messaging.providers.baileys.requests.post")
    def test_send_template_renders_params_and_sends_as_text(self, mock_post):
        mock_post.return_value.json.return_value = {"id": "3EB0SENT2"}
        mock_post.return_value.raise_for_status.return_value = None

        self.provider.send_template(
            "+573000000096", "Hola {{name}}, tu pedido {{order}} va en camino",
            {"name": "Ana", "order": "#123"},
        )

        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"]["body"], "Hola Ana, tu pedido #123 va en camino"
        )
