"""Rows written straight into the database by something other than this app.

The production database is shared with an automation that inserts customers,
conversations and messages without going through ``messaging.services``. That
writer has its own vocabulary, so it will eventually store a status, channel
or direction this app has never seen -- and the read side used to assume its
own enums were the only possible values.

The rule these tests pin down: an unrecognised value may look wrong, but it
must never take a screen down. A single odd row otherwise 500s the whole
Inbox for everybody, and the app offers no way to fix it from the UI.

The companion to this file is the written contract in README.md, which says
what the writer should store so rows look *right* rather than merely safe.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Client
from messaging.models import Conversation, Message
from messaging.providers.types import MessageStatus


def contact(phone="+573001112233", **kwargs):
    kwargs.setdefault("first_name", "Ana")
    kwargs.setdefault("last_name", "Real")
    return Client.objects.create(phone=phone, **kwargs)


def conversation(contact_obj=None, **kwargs):
    kwargs.setdefault("channel", "whatsapp")
    kwargs.setdefault("last_message_at", timezone.now())
    return Conversation.objects.create(contact=contact_obj or contact(), **kwargs)


def message(chat, **kwargs):
    kwargs.setdefault("direction", Message.OUTBOUND)
    kwargs.setdefault("body", "hola")
    kwargs.setdefault("status", MessageStatus.DELIVERED.value)
    kwargs.setdefault("timestamp", timezone.now())
    return Message.objects.create(conversation=chat, **kwargs)


class UnknownMessageStatusTests(TestCase):
    """``status`` is the one an external writer is most likely to get wrong --
    'accepted', 'success', 'error' and '' are all natural things to store."""

    def setUp(self):
        self.chat = conversation()

    def test_an_unknown_status_still_renders_the_thread(self):
        for status in ["accepted", "success", "SENT", "error", ""]:
            with self.subTest(status=status):
                Message.objects.all().delete()
                message(self.chat, status=status)

                response = self.client.get(
                    reverse("inbox_thread", args=[self.chat.pk])
                )

                self.assertEqual(response.status_code, 200)

    def test_an_unknown_status_shows_the_alert_icon_rather_than_guessing(self):
        # Better to say "something is off with this one" than to draw a tick
        # that claims a delivery nobody confirmed.
        self.assertEqual(
            message(self.chat, status="accepted").status_icon_template,
            "icons/alert-circle.svg",
        )

    def test_the_known_statuses_are_untouched(self):
        expected = {
            "queued": "icons/clock.svg",
            "sent": "icons/check.svg",
            "delivered": "icons/check-check.svg",
            "read": "icons/check-check.svg",
            "failed": "icons/alert-circle.svg",
        }
        for status, template in expected.items():
            with self.subTest(status=status):
                self.assertEqual(
                    Message(status=status).status_icon_template, template
                )


class UnknownConversationChannelTests(TestCase):
    """One bad channel used to 500 the whole Inbox list, not just its row."""

    def test_the_inbox_list_survives_an_unknown_channel(self):
        conversation(channel="wa")

        response = self.client.get(reverse("section", args=["inbox"]))

        self.assertEqual(response.status_code, 200)

    def test_an_open_thread_survives_an_unknown_channel(self):
        chat = conversation(channel="WhatsApp")
        message(chat)

        response = self.client.get(reverse("inbox_chat", args=[chat.pk]))

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_channel_falls_back_to_a_generic_mark(self):
        self.assertEqual(
            Conversation(channel="wa").icon_template, "icons/message-circle.svg"
        )

    def test_the_known_channels_keep_their_brand_marks(self):
        for channel, template in [
            ("whatsapp", "icons/brands/whatsapp.svg"),
            ("messenger", "icons/brands/messenger.svg"),
            ("instagram-dm", "icons/brands/instagram-dm.svg"),
            ("tiktok-dm", "icons/brands/tiktok.svg"),
            ("tiktok-coment", "icons/brands/tiktok.svg"),
        ]:
            with self.subTest(channel=channel):
                self.assertEqual(
                    Conversation(channel=channel).icon_template, template
                )


class UnknownClientChannelTests(TestCase):
    def test_the_clientes_table_survives_an_unknown_channel(self):
        contact(channel="WhatsApp Business")

        response = self.client.get(
            reverse("section", args=["crm"]), {"view": "clientes"}
        )

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_client_channel_shows_no_brand_mark(self):
        self.assertEqual(Client(channel="wa").icon_template, "")
        self.assertEqual(Client(channel="").icon_template, "")
        self.assertEqual(
            Client(channel="whatsapp").icon_template, "icons/brands/whatsapp.svg"
        )


class MissingLastInboundAtTests(TestCase):
    """``last_inbound_at`` is denormalized bookkeeping. An external writer
    that inserts the message but forgets the UPDATE leaves the composer
    disabled forever, with no way to reopen it from the UI."""

    def test_the_window_falls_back_to_the_newest_inbound_message(self):
        chat = conversation(last_inbound_at=None)
        message(chat, direction=Message.INBOUND, timestamp=timezone.now())

        self.assertTrue(chat.is_within_24h_window)

    def test_an_old_inbound_message_still_reads_as_closed(self):
        chat = conversation(last_inbound_at=None)
        message(
            chat,
            direction=Message.INBOUND,
            timestamp=timezone.now() - timedelta(hours=30),
        )

        self.assertFalse(chat.is_within_24h_window)

    def test_outbound_only_does_not_open_the_window(self):
        chat = conversation(last_inbound_at=None)
        message(chat, direction=Message.OUTBOUND)

        self.assertFalse(chat.is_within_24h_window)

    def test_a_conversation_with_no_messages_is_closed(self):
        self.assertFalse(conversation(last_inbound_at=None).is_within_24h_window)

    def test_the_stored_column_still_wins_when_it_is_set(self):
        # The fallback is a repair, not a replacement: the denormalized column
        # is what keeps the list query flat.
        chat = conversation(last_inbound_at=timezone.now())
        with self.assertNumQueries(0):
            self.assertTrue(chat.is_within_24h_window)


class UnknownDirectionTests(TestCase):
    """The reply-time report treated anything that was not 'inbound' as an
    agent reply, so a row written as 'INBOUND' scored as an instant answer."""

    def test_an_unknown_direction_is_ignored_not_counted_as_a_reply(self):
        from core import estadisticas_tiempos

        chat = conversation()
        start = timezone.now() - timedelta(hours=2)
        message(chat, direction=Message.INBOUND, timestamp=start)
        message(chat, direction="INBOUND", timestamp=start + timedelta(minutes=5))

        responses, _ = estadisticas_tiempos._collect(
            start - timedelta(hours=1), timezone.now(), platform=None
        )

        self.assertEqual(responses, [])


class PhoneNormalizationTests(TestCase):
    """WhatsApp reports a wa_id with no '+'. A writer that stores it verbatim
    creates a second contact for a customer the CRM already knows."""

    def test_a_number_without_a_plus_matches_the_stored_contact(self):
        from messaging import services

        existing = contact(phone="+573001112233")

        self.assertEqual(services._upsert_contact("573001112233", "Ana", "whatsapp"), existing)
        self.assertEqual(Client.objects.count(), 1)

    def test_a_stored_number_without_a_plus_is_matched_too(self):
        from messaging import services

        existing = contact(phone="573001112233")

        self.assertEqual(
            services._upsert_contact("+573001112233", "Ana", "whatsapp"), existing
        )
        self.assertEqual(Client.objects.count(), 1)

    def test_a_genuinely_new_number_is_stored_canonically(self):
        from messaging import services

        created = services._upsert_contact("57 300 444 5566", "Nueva", "whatsapp")

        self.assertEqual(created.phone, "+573004445566")

    def test_a_different_number_is_still_a_different_contact(self):
        from messaging import services

        contact(phone="+573001112233")
        services._upsert_contact("+573009998877", "Otra", "whatsapp")

        self.assertEqual(Client.objects.count(), 2)
