"""Tests for agents: the env-configured list, the login it backs, and the
Inbox's per-conversation assignment.

Credentials are pinned with ``override_settings`` rather than read from
whatever a developer's local .env holds -- ``core.agents`` re-reads settings on
every call precisely so this works.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import agents
from core.middleware import SESSION_KEY
from core.models import Client
from messaging.models import Conversation

TWO_AGENTS = "Admin:admin-pw:Admin,Samuel:1234:Samuel"


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class ConfiguredAgentsTests(TestCase):
    def test_entries_parse_in_env_order(self):
        parsed = agents.configured_agents()
        self.assertEqual([a.username for a in parsed], ["Admin", "Samuel"])
        self.assertEqual([a.display_name for a in parsed], ["Admin", "Samuel"])
        self.assertEqual(parsed[1].password, "1234")

    def test_display_name_is_optional(self):
        with override_settings(APP_AGENTS="Samuel:1234"):
            self.assertEqual(agents.configured_agents()[0].display_name, "Samuel")

    def test_whitespace_around_entries_is_ignored(self):
        with override_settings(APP_AGENTS=" Admin:admin-pw:Admin , Samuel:1234 "):
            self.assertEqual(
                [a.username for a in agents.configured_agents()], ["Admin", "Samuel"]
            )

    def test_malformed_entries_are_skipped_not_fatal(self):
        """One typo should cost that agent their login, not lock out the team."""
        with override_settings(APP_AGENTS="broken,:no-user,Samuel:1234,Empty:"):
            self.assertEqual(
                [a.username for a in agents.configured_agents()], ["Samuel"]
            )

    def test_duplicate_username_keeps_the_first(self):
        with override_settings(APP_AGENTS="Samuel:first,Samuel:second"):
            parsed = agents.configured_agents()
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].password, "first")

    def test_password_may_contain_spaces(self):
        with override_settings(APP_AGENTS="Samuel:una clave larga"):
            self.assertEqual(agents.configured_agents()[0].password, "una clave larga")

    def test_authenticate_matches_the_right_agent(self):
        self.assertEqual(agents.authenticate("Samuel", "1234").display_name, "Samuel")
        self.assertEqual(agents.authenticate("Admin", "admin-pw").username, "Admin")

    def test_authenticate_rejects_wrong_password_and_unknown_user(self):
        self.assertIsNone(agents.authenticate("Samuel", "12345"))
        self.assertIsNone(agents.authenticate("Nadie", "1234"))
        self.assertIsNone(agents.authenticate("", ""))

    def test_authenticate_does_not_cross_credentials_between_agents(self):
        """Samuel's password must not open Admin's account, or vice versa."""
        self.assertIsNone(agents.authenticate("Admin", "1234"))
        self.assertIsNone(agents.authenticate("Samuel", "admin-pw"))


class LegacyCredentialFallbackTests(TestCase):
    """An environment that predates APP_AGENTS keeps working untouched."""

    @override_settings(
        APP_AGENTS="", APP_LOGIN_USERNAME="viejo", APP_LOGIN_PASSWORD="clave"
    )
    def test_single_pair_becomes_the_only_agent(self):
        parsed = agents.configured_agents()
        self.assertEqual([a.username for a in parsed], ["viejo"])
        self.assertIsNotNone(agents.authenticate("viejo", "clave"))

    @override_settings(
        APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="viejo", APP_LOGIN_PASSWORD="clave"
    )
    def test_app_agents_wins_when_both_are_set(self):
        self.assertEqual(
            [a.username for a in agents.configured_agents()], ["Admin", "Samuel"]
        )
        self.assertIsNone(agents.authenticate("viejo", "clave"))

    @override_settings(APP_AGENTS="", APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
    def test_nothing_configured_means_nobody_gets_in(self):
        self.assertEqual(agents.configured_agents(), [])
        self.assertIsNone(agents.authenticate("", ""))


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class AgentUserMirrorTests(TestCase):
    def test_mirror_rows_are_created_on_demand_in_env_order(self):
        users = agents.agent_users()
        self.assertEqual([u.username for u in users], ["Admin", "Samuel"])
        self.assertEqual(users[1].first_name, "Samuel")

    def test_mirrors_are_reused_not_duplicated(self):
        first = agents.agent_users()
        second = agents.agent_users()
        self.assertEqual([u.pk for u in first], [u.pk for u in second])
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_mirror_cannot_authenticate_through_the_orm(self):
        """The env list is the only way in -- the User row is just an assignee."""
        user = agents.agent_users()[1]
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.check_password("1234"))

    def test_an_agent_dropped_from_the_env_stops_being_listed(self):
        agents.agent_users()
        with override_settings(APP_AGENTS="Admin:admin-pw:Admin"):
            self.assertEqual([u.username for u in agents.agent_users()], ["Admin"])
        # ...but the row survives, so conversations assigned to them keep a name.
        self.assertTrue(get_user_model().objects.filter(username="Samuel").exists())

    def test_no_agents_configured_yields_no_options(self):
        with override_settings(APP_AGENTS=""):
            self.assertEqual(agents.agent_users(), [])


@override_settings(
    TESTING=False, APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""
)
class AgentLoginTests(TestCase):
    """The gate now starts a real auth session, so an agent is an identity."""

    def test_each_agent_can_log_in_with_their_own_password(self):
        for username, password in (("Admin", "admin-pw"), ("Samuel", "1234")):
            with self.subTest(agent=username):
                self.client.post(
                    reverse("login"), {"username": username, "password": password}
                )
                self.assertTrue(self.client.session.get(SESSION_KEY))
                self.assertEqual(
                    get_user_model().objects.get(pk=self.client.session["_auth_user_id"]).username,
                    username,
                )
                self.client.get(reverse("logout"))

    def test_login_makes_request_user_that_agent(self):
        self.client.post(reverse("login"), {"username": "Samuel", "password": "1234"})
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertEqual(response.wsgi_request.user.username, "Samuel")

    def test_wrong_password_starts_no_auth_session(self):
        response = self.client.post(
            reverse("login"), {"username": "Samuel", "password": "nope"}
        )
        self.assertContains(response, "incorrectos")
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_clears_the_auth_session_too(self):
        self.client.post(reverse("login"), {"username": "Samuel", "password": "1234"})
        self.client.get(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class ConversationAssignmentTests(TestCase):
    def setUp(self):
        self.contact = Client.objects.create(
            first_name="Ana", last_name="Ruiz", phone="+573000000001", channel="whatsapp"
        )
        self.conversation = Conversation.objects.create(
            contact=self.contact,
            channel="whatsapp",
            # Inside the 24h window, so the composer (and its Respuestas
            # rápidas button) renders instead of the closed-window notice.
            last_inbound_at=timezone.now(),
        )
        self.admin, self.samuel = agents.agent_users()

    def url(self):
        return reverse("inbox_assign", args=[self.conversation.pk])

    def test_posting_an_agent_id_assigns_the_conversation(self):
        response = self.client.post(self.url(), {"agent": str(self.samuel.pk)})
        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.samuel)

    def test_posting_a_blank_agent_unassigns(self):
        self.conversation.assigned_to = self.samuel
        self.conversation.save(update_fields=["assigned_to"])

        self.client.post(self.url(), {"agent": ""})
        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.assigned_to)

    def test_reassigning_replaces_rather_than_adds(self):
        self.client.post(self.url(), {"agent": str(self.samuel.pk)})
        self.client.post(self.url(), {"agent": str(self.admin.pk)})
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.assigned_to, self.admin)

    def test_a_user_who_is_not_a_configured_agent_is_rejected(self):
        """The dropdown is a fixed list, so anything else is a crafted POST."""
        outsider = get_user_model().objects.create_user("intruso", password="x")
        response = self.client.post(self.url(), {"agent": str(outsider.pk)})
        self.assertEqual(response.status_code, 400)
        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.assigned_to)

    def test_a_nonsense_agent_id_is_rejected(self):
        response = self.client.post(self.url(), {"agent": "no-soy-un-id"})
        self.assertEqual(response.status_code, 400)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_unknown_conversation_404s(self):
        response = self.client.post(reverse("inbox_assign", args=[9999]), {"agent": ""})
        self.assertEqual(response.status_code, 404)

    def test_response_carries_the_new_name_for_both_panels(self):
        """The control comes back as the swap target; the details panel's
        "Asignada a" line rides along out-of-band."""
        response = self.client.post(self.url(), {"agent": str(self.samuel.pk)})
        html = response.content.decode()
        self.assertIn(f'id="chat-assign-{self.conversation.pk}"', html)
        self.assertIn(f'id="details-assigned-{self.conversation.pk}"', html)
        self.assertIn('hx-swap-oob="outerHTML"', html)
        self.assertIn("Samuel", html)

    def test_chat_panel_renders_the_dropdown_with_every_agent(self):
        self.conversation.assigned_to = self.samuel
        self.conversation.save(update_fields=["assigned_to"])

        response = self.client.get(reverse("inbox_chat", args=[self.conversation.pk]))
        html = response.content.decode()
        self.assertIn("Sin asignar", html)
        self.assertIn(f'value="{self.admin.pk}"', html)
        self.assertInHTML(
            f'<option value="{self.samuel.pk}" selected>Samuel</option>', html
        )

    def test_an_assignee_dropped_from_the_env_still_shows_as_assigned(self):
        """A <select> with no matching option silently shows its first entry --
        which would claim an assigned chat is "Sin asignar"."""
        self.conversation.assigned_to = self.samuel
        self.conversation.save(update_fields=["assigned_to"])

        with override_settings(APP_AGENTS="Admin:admin-pw:Admin"):
            response = self.client.get(
                reverse("inbox_chat", args=[self.conversation.pk])
            )
        self.assertInHTML(
            f'<option value="{self.samuel.pk}" selected>Samuel</option>',
            response.content.decode(),
        )

    def test_reassigning_away_drops_a_non_agent_from_the_options(self):
        """The old assignee is only listed to keep them visible while they
        hold the chat -- once they don't, they shouldn't linger in the list."""
        outsider = get_user_model().objects.create_user("asesor", password="x")
        outsider.first_name = "Asesor Demo"
        outsider.save()
        self.conversation.assigned_to = outsider
        self.conversation.save(update_fields=["assigned_to"])

        response = self.client.post(self.url(), {"agent": str(self.samuel.pk)})
        self.assertNotIn("Asesor Demo", response.content.decode())

    def test_composer_offers_quick_replies(self):
        """The picker is wired to its endpoint (tests_respuestas_rapidas has
        the behavior; this pins that the composer carries it)."""
        response = self.client.get(reverse("inbox_chat", args=[self.conversation.pk]))
        html = response.content.decode()
        self.assertIn("Respuestas rápidas", html)
        self.assertIn(reverse("inbox_quick_replies", args=[self.conversation.pk]), html)


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class TuInboxFilterTests(TestCase):
    """"Tu inbox" was dead while nobody had an identity; agents give it one."""

    def setUp(self):
        contact = Client.objects.create(
            first_name="Ana", phone="+573000000001", channel="whatsapp"
        )
        self.admin, self.samuel = agents.agent_users()
        self.mine = Conversation.objects.create(
            contact=contact, channel="whatsapp", assigned_to=self.samuel
        )
        self.theirs = Conversation.objects.create(
            contact=contact, channel="whatsapp", assigned_to=self.admin
        )
        self.nobodys = Conversation.objects.create(contact=contact, channel="whatsapp")

    def test_tu_inbox_shows_only_the_logged_in_agents_conversations(self):
        self.client.force_login(self.samuel)
        response = self.client.get(reverse("inbox_list", args=["tu-inbox"]))
        html = response.content.decode()
        self.assertIn(f"/inbox/chat/{self.mine.pk}/", html)
        self.assertNotIn(f"/inbox/chat/{self.theirs.pk}/", html)

    def test_sin_asignar_shows_the_unassigned_one(self):
        response = self.client.get(reverse("inbox_list", args=["sin-asignar"]))
        html = response.content.decode()
        self.assertIn(f"/inbox/chat/{self.nobodys.pk}/", html)
        self.assertNotIn(f"/inbox/chat/{self.mine.pk}/", html)
