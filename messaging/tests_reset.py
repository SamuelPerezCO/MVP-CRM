"""The reset_conversations command: it must not delete without --yes."""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import CalendarEvent, Client
from messaging.models import Conversation, Message, Tag
from messaging.providers.types import MessageStatus


class ResetConversationsTests(TestCase):
    def setUp(self):
        self.contact = Client.objects.create(
            first_name="Cliente", last_name="Prueba", phone="+573000000099"
        )
        self.conversation = Conversation.objects.create(
            contact=self.contact, channel="whatsapp"
        )
        Message.objects.create(
            conversation=self.conversation,
            direction=Message.INBOUND,
            body="hola",
            status=MessageStatus.DELIVERED.value,
            provider_message_id="wamid.RESET1",
            timestamp=timezone.now(),
        )

    def run_command(self, *args):
        out = StringIO()
        call_command("reset_conversations", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_deletes_nothing(self):
        """The default must be harmless: this runs against production."""
        output = self.run_command()

        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Client.objects.count(), 1)
        self.assertIn("Simulación", output)

    def test_dry_run_names_the_database_it_would_touch(self):
        """A wipe pointed at the wrong DATABASE_URL is unrecoverable, so the
        target has to be visible before you type --yes."""
        self.assertIn("Base de datos:", self.run_command())

    def test_yes_empties_the_inbox(self):
        self.run_command("--yes")

        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)

    def test_keep_clients_preserves_contacts(self):
        self.run_command("--yes", "--keep-clients")

        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 1)

    def test_tags_survive(self):
        """Tags are configuration, not conversation data."""
        Tag.objects.create(name="Primer contacto")

        self.run_command("--yes")

        self.assertEqual(Tag.objects.count(), 1)

    def test_calendar_events_survive_their_contact(self):
        """CalendarEvent.contact is SET_NULL -- the event outlives the client."""
        event = CalendarEvent.objects.create(
            title="Llamada",
            start=timezone.now(),
            end=timezone.now(),
            contact=self.contact,
        )

        self.run_command("--yes")

        event.refresh_from_db()
        self.assertIsNone(event.contact)

    def test_empty_inbox_is_a_no_op(self):
        self.run_command("--yes")
        output = self.run_command("--yes")

        self.assertIn("ya está vacío", output)
