"""Tests for going live: the clean-slate purge and the guards around the
tools that invent data.

Three things have to hold before real customers arrive:

* ``go_live`` empties the CRM but never the team -- losing the accounts would
  lock everyone out of the app they just cleaned.
* ``seed_conversations`` / ``simulate_inbound`` cannot write fixtures into the
  production database, however the environment is pointed.
* The fake provider's webhook, whose shared secret is published in
  ``.env.example``, does not answer on a real deployment.
"""

from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from unittest.mock import patch

from core import agents
from core.models import (
    CalendarEvent,
    Client,
    ClientList,
    MessageTemplate,
    Product,
    QuickReply,
)
from messaging.models import Conversation, ConversationTag, Message, Tag

ONE_AGENT = "Samuel:una-clave-larga:Samuel"


def run(*args) -> str:
    out = StringIO()
    call_command("go_live", *args, stdout=out)
    return out.getvalue()


@override_settings(APP_AGENTS="", APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class GoLiveTests(TestCase):
    """The purge itself."""

    def setUp(self):
        User = get_user_model()

        # The team: one login created the way CRM > Equipo > Usuarios does.
        self.teammate = agents.create_user(
            "samuel", "una-clave-larga", "Samuel Pérez", master=True
        )

        # A fixture account: assignable, no way in. This is what a seed leaves.
        self.fixture = User.objects.create(
            username="asesor", first_name="Asesor", last_name="Demo"
        )
        self.fixture.set_unusable_password()
        self.fixture.save()

        self.contact = Client.objects.create(
            first_name="Camila", last_name="Pruebas", phone="+573000000001",
            channel="whatsapp",
        )
        self.conversation = Conversation.objects.create(
            contact=self.contact, channel="whatsapp", assigned_to=self.fixture,
            last_inbound_at=timezone.now(),
        )
        Message.objects.create(
            conversation=self.conversation, direction=Message.INBOUND,
            body="Hola", provider_message_id="seed-1", timestamp=timezone.now(),
        )
        self.tag = Tag.objects.create(name="CLIENTE NUEVO", color="yellow",
                                      created_by=self.fixture)
        ConversationTag.objects.create(conversation=self.conversation, tag=self.tag,
                                       tagged_by=self.fixture)
        CalendarEvent.objects.create(
            title="Llamada de bienvenida a Camila",
            description="Evento de demostración.",
            start=timezone.now(), end=timezone.now(),
            assigned_to=self.fixture, created_by=self.fixture,
        )
        ClientList.objects.create(name="Lista de prueba")
        Product.objects.create(name="Camiseta", price=50000)
        MessageTemplate.objects.create(name="bienvenida", body="Hola {{1}}")
        QuickReply.objects.create(title="Horario", body="De 9 a 6.")

    def test_dry_run_changes_nothing(self):
        output = run()
        self.assertIn("Simulación", output)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_dry_run_reports_what_it_would_delete(self):
        output = run()
        for label in ("mensajes", "conversaciones", "contactos", "etiquetas",
                      "eventos de calendario", "cuentas de prueba"):
            self.assertIn(label, output)

    def test_yes_empties_the_crm(self):
        run("--yes")
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(ConversationTag.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(Tag.objects.count(), 0)
        self.assertEqual(CalendarEvent.objects.count(), 0)
        self.assertEqual(ClientList.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(MessageTemplate.objects.count(), 0)
        self.assertEqual(QuickReply.objects.count(), 0)

    def test_the_team_survives_and_the_fixtures_do_not(self):
        run("--yes")
        usernames = list(get_user_model().objects.values_list("username", flat=True))
        self.assertEqual(usernames, ["samuel"])

    def test_the_seeded_advisor_goes_even_with_a_password(self):
        """seed_conversations gives `asesor` a real password so /admin works,
        which would otherwise read as "a person created this account" and keep
        the fixture through the purge meant to remove it."""
        self.fixture.set_password("asesor123")
        self.fixture.save(update_fields=["password"])
        run("--yes")
        self.assertFalse(get_user_model().objects.filter(username="asesor").exists())

    def test_the_surviving_login_still_works(self):
        """The whole point: whoever runs this can still get in afterwards."""
        run("--yes")
        self.assertIsNotNone(agents.authenticate("samuel", "una-clave-larga"))

    def test_a_deactivated_teammate_is_kept(self):
        self.teammate.is_active = False
        self.teammate.save(update_fields=["is_active"])
        run("--yes")
        self.assertTrue(get_user_model().objects.filter(username="samuel").exists())

    def test_a_superuser_is_kept(self):
        get_user_model().objects.create_superuser("root", password="x" * 12)
        run("--yes")
        self.assertTrue(get_user_model().objects.filter(username="root").exists())

    @override_settings(APP_AGENTS=ONE_AGENT)
    def test_an_env_configured_agent_is_kept(self):
        agents.agent_users()  # materializes the mirror row, as the Inbox does
        run("--yes")
        self.assertTrue(get_user_model().objects.filter(username="Samuel").exists())

    def test_keep_catalog_leaves_the_catalog(self):
        run("--yes", "--keep-catalog")
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(MessageTemplate.objects.count(), 1)
        self.assertEqual(QuickReply.objects.count(), 1)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_names_the_database_before_touching_it(self):
        self.assertIn("Base de datos:", run())

    def test_lists_the_accounts_it_keeps(self):
        self.assertIn("samuel", run())

    def test_running_twice_is_harmless(self):
        run("--yes")
        output = run("--yes")
        self.assertIn("ya está vacío", output)

    def test_warns_when_no_account_would_survive(self):
        """A real password is what marks a row as a login (agents.is_app_user),
        so a team with none of those is a team nothing would keep."""
        self.teammate.set_unusable_password()
        self.teammate.save(update_fields=["password"])
        self.assertIn("Ninguna cuenta del equipo sobrevive", run())


class DevCommandGuardTests(TestCase):
    """seed_conversations and simulate_inbound refuse a non-local database."""

    REMOTE = {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "ep-nameless-lake.aws.neon.tech",
        "NAME": "neondb",
    }

    def test_seed_refuses_a_postgres_database(self):
        with patch("django.db.connection.settings_dict", self.REMOTE):
            with self.assertRaises(CommandError) as caught:
                call_command("seed_conversations", stdout=StringIO())
        self.assertIn("solo puede correr contra la base local", str(caught.exception))

    def test_simulate_inbound_refuses_a_postgres_database(self):
        with patch("django.db.connection.settings_dict", self.REMOTE):
            with self.assertRaises(CommandError):
                call_command(
                    "simulate_inbound", "+573000000001", "Hola", stdout=StringIO()
                )

    def test_the_error_names_the_database_it_stopped(self):
        with patch("django.db.connection.settings_dict", self.REMOTE):
            with self.assertRaises(CommandError) as caught:
                call_command("simulate_inbound", "+57300", "Hola", stdout=StringIO())
        self.assertIn("neon.tech", str(caught.exception))

    @patch.dict("os.environ", {"ALLOW_DEV_COMMANDS_ON_REMOTE_DB": "1"})
    def test_the_documented_override_lets_it_through(self):
        """Deliberate, not accidental: an env var, never a --force flag."""
        with patch("django.db.connection.settings_dict", self.REMOTE):
            call_command("simulate_inbound", "+573000000001", "Hola", stdout=StringIO())
        self.assertTrue(Message.objects.exists())

    def test_sqlite_is_allowed(self):
        call_command("simulate_inbound", "+573000000002", "Hola", stdout=StringIO())
        self.assertTrue(Message.objects.exists())


class FakeWebhookExposureTests(TestCase):
    """The simulator's door is shut on a real deployment.

    ``MESSAGING_FAKE_SECRET`` defaults to ``dev-secret``, a value published in
    .env.example and the README -- so while this webhook answers, anyone who
    has read the repo can post invented customers into the Inbox.
    """

    def url(self, provider: str) -> str:
        return reverse("messaging_webhook", args=[provider])

    def post(self, provider: str, **headers):
        return self.client.post(
            self.url(provider), data="{}", content_type="application/json", **headers
        )

    @override_settings(TESTING=False, DEBUG=False)
    def test_disabled_on_a_production_like_deployment(self):
        self.assertEqual(self.post("fake").status_code, 404)

    @override_settings(TESTING=False, DEBUG=False)
    def test_a_valid_signature_does_not_reopen_it(self):
        """Not a signature check -- the endpoint is simply not there."""
        response = self.post(
            "fake", headers={"X-Fake-Signature": "dev-secret"}
        )
        self.assertEqual(response.status_code, 404)

    @override_settings(TESTING=False, DEBUG=True)
    def test_local_development_keeps_it(self):
        # 401, not 404: the door is open, the unsigned request is what fails.
        self.assertEqual(self.post("fake").status_code, 401)

    @override_settings(TESTING=False, DEBUG=False, MESSAGING_ALLOW_FAKE_WEBHOOK=True)
    def test_staging_can_opt_back_in(self):
        self.assertEqual(self.post("fake").status_code, 401)

    @override_settings(TESTING=False, DEBUG=False)
    def test_real_providers_are_untouched(self):
        # Twilio is left out: its provider is still a stub that raises rather
        # than verifying, so it has no signature behaviour to assert yet.
        for provider in ("meta", "baileys"):
            with self.subTest(provider=provider):
                self.assertEqual(self.post(provider).status_code, 401)

    def test_the_test_runner_keeps_it(self):
        """Every other messaging test posts to this URL."""
        self.assertEqual(self.post("fake").status_code, 401)
