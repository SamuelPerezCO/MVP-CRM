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
        self.assertEqual(parsed[1].secret, "1234")

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
            self.assertEqual(parsed[0].secret, "first")

    def test_password_may_contain_spaces(self):
        with override_settings(APP_AGENTS="Samuel:una clave larga"):
            self.assertEqual(agents.configured_agents()[0].secret, "una clave larga")

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

    def test_a_user_who_is_not_an_agent_is_rejected(self):
        """The dropdown is a fixed list, so anything else is a crafted POST.
        A row with no usable password (a seed's, /admin's) is not an agent --
        users created from the Usuarios page have one and ARE."""
        outsider = get_user_model().objects.create_user("intruso")
        outsider.set_unusable_password()
        outsider.save()
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
        outsider = get_user_model().objects.create_user("asesor")
        outsider.set_unusable_password()   # not an agent: no login of its own
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


# --- Hashed environment secrets ---------------------------------------------


def env_entry(username, password, name=None):
    """An APP_AGENTS entry with a real hash in it, the way an operator would
    paste one from `manage.py hashear_clave`."""
    from django.contrib.auth.hashers import make_password

    return f"{username}:{make_password(password)}:{name or username}"


class EnvSecretShapeTests(TestCase):
    """What counts as a hash, and what is a raw password wearing a $."""

    def test_a_real_hash_is_recognised(self):
        from django.contrib.auth.hashers import make_password

        self.assertTrue(agents.Agent("x", make_password("y"), "X").is_hashed)

    def test_plain_passwords_are_not(self):
        for secret in ("1234", "una clave larga", "", "no-dollar-here"):
            with self.subTest(secret):
                self.assertFalse(agents.Agent("x", secret, "X").is_hashed)

    def test_a_password_containing_a_dollar_is_not_a_hash(self):
        for secret in ("me$4gusta", "$$$", "sha999$abc$def"):
            with self.subTest(secret):
                self.assertFalse(agents.Agent("x", secret, "X").is_hashed)

    def test_a_32_character_password_is_not_read_as_unsalted_md5(self):
        """identify_hasher would; that's why core.agents doesn't use it."""
        self.assertFalse(agents.Agent("x", "a" * 32, "X").is_hashed)

    def test_hashes_carry_no_app_agents_separator(self):
        from django.contrib.auth.hashers import make_password

        encoded = make_password("clave-segura-1")
        self.assertNotIn(":", encoded)
        self.assertNotIn(",", encoded)


class HashedEnvLoginTests(TestCase):
    def setUp(self):
        self.entries = ",".join(
            [env_entry("Admin", "clave-de-admin"), env_entry("Samuel", "clave-de-samuel")]
        )

    def settings(self, **extra):
        return override_settings(
            APP_AGENTS=self.entries, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="", **extra
        )

    def test_the_environment_holds_no_readable_password(self):
        for password in ("clave-de-admin", "clave-de-samuel"):
            with self.subTest(password):
                self.assertNotIn(password, self.entries)

    def test_every_agent_is_hashed(self):
        with self.settings():
            self.assertTrue(all(a.is_hashed for a in agents.configured_agents()))

    def test_the_right_password_authenticates(self):
        with self.settings():
            self.assertEqual(agents.authenticate("Admin", "clave-de-admin").username, "Admin")
            self.assertEqual(agents.authenticate("Samuel", "clave-de-samuel").username, "Samuel")

    def test_a_wrong_password_does_not(self):
        with self.settings():
            self.assertIsNone(agents.authenticate("Admin", "clave-de-samuel"))
            self.assertIsNone(agents.authenticate("Admin", "clave-de-admin "))
            self.assertIsNone(agents.authenticate("Nadie", "clave-de-admin"))

    def test_the_hash_itself_is_not_a_password(self):
        """Someone who reads the env and pastes what they found gets nowhere."""
        encoded = self.entries.split(":")[1]
        with self.settings():
            self.assertIsNone(agents.authenticate("Admin", encoded))

    def test_logging_in_through_the_view(self):
        with self.settings(TESTING=False):
            response = self.client.post(
                reverse("login"), {"username": "Samuel", "password": "clave-de-samuel"}
            )
            self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
            self.assertTrue(self.client.session.get(SESSION_KEY))

    def test_a_hashed_agent_still_cannot_log_in_through_the_database(self):
        """The mirror row keeps an unusable password: the env is the one door."""
        with self.settings():
            mirror = agents.authenticate("Admin", "clave-de-admin").user
            self.assertFalse(mirror.has_usable_password())
            self.assertFalse(mirror.check_password("clave-de-admin"))

    def test_non_ascii_passwords_survive_hashing(self):
        with override_settings(
            APP_AGENTS=env_entry("José", "contraseña-ñ"),
            APP_LOGIN_USERNAME="",
            APP_LOGIN_PASSWORD="",
        ):
            self.assertIsNotNone(agents.authenticate("José", "contraseña-ñ"))
            self.assertIsNone(agents.authenticate("José", "contrasena-n"))

    def test_a_non_ascii_username_never_raises(self):
        """compare_digest on str raises for non-ASCII; that was a 500."""
        with self.settings():
            self.assertIsNone(agents.authenticate("José", "x"))

    def test_the_legacy_pair_may_be_hashed_too(self):
        from django.contrib.auth.hashers import make_password

        with override_settings(
            APP_AGENTS="",
            APP_LOGIN_USERNAME="viejo",
            APP_LOGIN_PASSWORD=make_password("clave-vieja"),
        ):
            self.assertIsNotNone(agents.authenticate("viejo", "clave-vieja"))
            self.assertIsNone(agents.authenticate("viejo", "otra"))

    def test_plaintext_entries_still_work_alongside_hashed_ones(self):
        """Deprecated, warned about, but never a lockout on redeploy."""
        mixed = f"{env_entry('Admin', 'clave-de-admin')},Viejo:en-claro:Viejo"
        with override_settings(APP_AGENTS=mixed, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""):
            self.assertIsNotNone(agents.authenticate("Admin", "clave-de-admin"))
            self.assertIsNotNone(agents.authenticate("Viejo", "en-claro"))
            self.assertIsNone(agents.authenticate("Viejo", "otra"))


class PlaintextWarningTests(TestCase):
    def run_check(self):
        from core.checks import plaintext_env_secrets

        return plaintext_env_secrets(None)

    @override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
    def test_plaintext_agents_are_named_in_the_warning(self):
        (warning,) = self.run_check()
        self.assertEqual(warning.id, "core.W001")
        self.assertIn("'Admin'", warning.msg)
        self.assertIn("'Samuel'", warning.msg)
        self.assertIn("hashear_clave", warning.hint)

    def test_hashed_agents_raise_nothing(self):
        entries = ",".join([env_entry("Admin", "clave-de-admin"), env_entry("Samuel", "clave-x")])
        with override_settings(APP_AGENTS=entries, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""):
            self.assertEqual(self.run_check(), [])

    def test_only_the_plaintext_ones_are_named(self):
        mixed = f"{env_entry('Admin', 'clave-de-admin')},Viejo:en-claro:Viejo"
        with override_settings(APP_AGENTS=mixed, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""):
            (warning,) = self.run_check()
            self.assertIn("'Viejo'", warning.msg)
            self.assertNotIn("'Admin'", warning.msg)

    @override_settings(APP_AGENTS="", APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
    def test_nothing_configured_raises_nothing(self):
        self.assertEqual(self.run_check(), [])

    @override_settings(APP_AGENTS="", APP_LOGIN_USERNAME="viejo", APP_LOGIN_PASSWORD="en-claro")
    def test_the_legacy_pair_is_checked_too(self):
        (warning,) = self.run_check()
        self.assertIn("'viejo'", warning.msg)


class HashearClaveCommandTests(TestCase):
    def run_command(self, *args, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("hashear_clave", *args, stdout=out, **kwargs)
        return out.getvalue().strip()

    def test_prints_a_pasteable_entry_that_actually_logs_in(self):
        entry = self.run_command("Samuel", "--name", "Samuel", "--password", "clave-segura-1")
        self.assertTrue(entry.startswith("Samuel:"))
        self.assertTrue(entry.endswith(":Samuel"))
        self.assertNotIn("clave-segura-1", entry)
        with override_settings(APP_AGENTS=entry, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""):
            self.assertEqual([a.username for a in agents.configured_agents()], ["Samuel"])
            self.assertEqual(agents.authenticate("Samuel", "clave-segura-1").display_name, "Samuel")

    def test_without_a_username_it_prints_only_the_hash(self):
        encoded = self.run_command("--password", "clave-segura-1")
        self.assertTrue(agents.Agent("x", encoded, "X").is_hashed)
        self.assertNotIn(":", encoded)

    def test_it_applies_a_password_floor(self):
        from django.core.management import CommandError

        for bad, message in (("corta", "al menos 8"), ("12345678", "solo números")):
            with self.subTest(bad):
                with self.assertRaisesMessage(CommandError, message):
                    self.run_command("samuel", "--password", bad)

    def test_several_usernames_print_the_whole_variable(self):
        from unittest.mock import patch

        with patch(
            "core.management.commands.hashear_clave.getpass",
            side_effect=["clave-de-admin", "clave-de-admin", "clave-de-samuel", "clave-de-samuel"],
        ):
            line = self.run_command("Admin", "Samuel")

        self.assertTrue(line.startswith("APP_AGENTS="))
        for password in ("clave-de-admin", "clave-de-samuel"):
            self.assertNotIn(password, line)

        with override_settings(
            APP_AGENTS=line.split("=", 1)[1], APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""
        ):
            self.assertEqual([a.username for a in agents.configured_agents()], ["Admin", "Samuel"])
            self.assertTrue(all(a.is_hashed for a in agents.configured_agents()))
            self.assertIsNotNone(agents.authenticate("Admin", "clave-de-admin"))
            self.assertIsNone(agents.authenticate("Admin", "clave-de-samuel"))

    def test_several_usernames_reject_the_single_agent_flags(self):
        from django.core.management import CommandError

        for flag in ("--name", "--password"):
            with self.subTest(flag):
                with self.assertRaisesMessage(CommandError, "un solo usuario"):
                    self.run_command("Admin", "Samuel", flag, "x")

    def test_a_mistyped_confirmation_is_refused(self):
        from unittest.mock import patch

        from django.core.management import CommandError

        with patch("core.management.commands.hashear_clave.getpass", side_effect=["uno", "dos"]):
            with self.assertRaisesMessage(CommandError, "no coinciden"):
                self.run_command("samuel")
