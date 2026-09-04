"""Tests for user management: the master role, the Usuarios page, the rules
that keep a team from locking itself out, and the database login path.

Credentials are pinned with ``override_settings``; ``core.agents`` re-reads
settings on every call precisely so this works. ``force_login`` stands in for
the login view where only the identity matters.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import agents, usuarios
from core.crm import visible_sections
from core.middleware import SESSION_KEY
from core.models import Client
from messaging.models import Conversation

User = get_user_model()

ENV = "Admin:admin-pw:Admin,Samuel:1234:Samuel"
NO_ENV = dict(APP_AGENTS="", APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")


def make_db_user(username, *, name=None, master=False, password="clave-segura-1", active=True):
    user = User(
        username=username,
        first_name=name or username.capitalize(),
        is_superuser=master,
        is_active=active,
    )
    user.set_password(password)
    user.save()
    return user


# --- Roles and the env/database split --------------------------------------


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class RoleTests(TestCase):
    def test_env_agents_are_masters(self):
        for user in agents.env_users():
            with self.subTest(user.username):
                self.assertTrue(user.is_superuser)
                self.assertTrue(agents.is_master(user))

    def test_env_shape_is_reasserted_on_sync(self):
        """A mirror edited from /admin, or a database row the env later
        claimed, snaps back: master, active, no database password."""
        admin = agents.env_users()[0]
        admin.is_superuser = False
        admin.is_active = False
        admin.set_password("puerta-trasera")
        admin.save()

        admin = agents.env_users()[0]
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)
        self.assertFalse(admin.has_usable_password())

    def test_database_users_are_not_env_managed(self):
        user = make_db_user("pepe")
        self.assertFalse(agents.is_env_managed(user))
        self.assertTrue(agents.is_env_managed(agents.env_users()[0]))

    def test_anonymous_is_never_a_master(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(agents.is_master(AnonymousUser()))

    def test_assignable_users_are_env_then_active_database_by_name(self):
        make_db_user("zoe", name="Zoe")
        make_db_user("ana", name="Ana")
        make_db_user("fuera", name="Fuera", active=False)
        self.assertEqual(
            [u.username for u in agents.agent_users()],
            ["Admin", "Samuel", "ana", "zoe"],
        )

    def test_deactivated_assignee_still_shows_on_their_conversation(self):
        gone = make_db_user("gone", active=False)
        contact = Client.objects.create(first_name="Ana", phone="+573000000001")
        conversation = Conversation.objects.create(
            contact=contact, channel="whatsapp", assigned_to=gone
        )
        self.assertIn(gone, agents.assignment_options(conversation))


# --- Logging in with a database account -------------------------------------


@override_settings(TESTING=False, APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class DatabaseLoginTests(TestCase):
    def test_database_user_can_log_in_with_their_password(self):
        make_db_user("pepe", password="clave-segura-1")
        self.client.post(reverse("login"), {"username": "pepe", "password": "clave-segura-1"})
        self.assertTrue(self.client.session.get(SESSION_KEY))
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertEqual(response.wsgi_request.user.username, "pepe")

    def test_wrong_database_password_is_rejected(self):
        make_db_user("pepe", password="clave-segura-1")
        response = self.client.post(reverse("login"), {"username": "pepe", "password": "nope"})
        self.assertContains(response, "incorrectos")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_env_agents_still_log_in_through_the_env(self):
        self.client.post(reverse("login"), {"username": "Samuel", "password": "1234"})
        self.assertTrue(self.client.session.get(SESSION_KEY))

    def test_env_username_never_falls_through_to_the_database(self):
        """Even if a row with an env username somehow held a usable password,
        the env is the only door for that name."""
        make_db_user("Samuel", password="clave-segura-1")
        response = self.client.post(
            reverse("login"), {"username": "Samuel", "password": "clave-segura-1"}
        )
        self.assertContains(response, "incorrectos")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_deactivated_user_cannot_log_in(self):
        make_db_user("pepe", password="clave-segura-1", active=False)
        response = self.client.post(reverse("login"), {"username": "pepe", "password": "clave-segura-1"})
        self.assertContains(response, "incorrectos")

    def test_deactivating_a_logged_in_user_locks_them_out_on_the_next_request(self):
        pepe = make_db_user("pepe", password="clave-segura-1")
        self.client.post(reverse("login"), {"username": "pepe", "password": "clave-segura-1"})
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

        pepe.is_active = False
        pepe.save()
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        # The dead session was flushed, not left half-alive.
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_deleting_a_logged_in_user_locks_them_out(self):
        pepe = make_db_user("pepe", password="clave-segura-1")
        self.client.post(reverse("login"), {"username": "pepe", "password": "clave-segura-1"})
        pepe.delete()
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_password_change_signs_the_old_session_out(self):
        pepe = make_db_user("pepe", password="clave-segura-1")
        self.client.post(reverse("login"), {"username": "pepe", "password": "clave-segura-1"})
        pepe.set_password("otra-clave-segura")
        pepe.save()
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)


# --- The Usuarios page ------------------------------------------------------


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class UsuariosPageTests(TestCase):
    def setUp(self):
        self.admin, self.samuel = agents.env_users()
        self.pepe = make_db_user("pepe", name="Pepe")

    def test_masters_see_the_equipo_section_and_usuarios_row(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn("Equipo", html)
        self.assertIn("?view=usuarios", html)

    def test_agents_do_not_see_the_row_nor_the_section(self):
        self.client.force_login(self.pepe)
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertNotIn("?view=usuarios", html)
        self.assertNotIn("Equipo", html)
        # The other sections are untouched.
        self.assertIn("Gestión de clientes", html)

    def test_visible_sections_drops_an_emptied_section(self):
        self.assertEqual([s.key for s in visible_sections(self.pepe)], ["gestion-clientes", "calendario"])
        self.assertEqual(
            [s.key for s in visible_sections(self.admin)],
            ["gestion-clientes", "calendario", "equipo"],
        )

    def test_agent_hitting_the_view_url_falls_back_to_the_default(self):
        self.client.force_login(self.pepe)
        response = self.client.get(reverse("section", args=["crm"]), {"view": "usuarios"})
        self.assertEqual(response.context["active_view"], "clientes")

    def test_agent_hitting_the_panel_endpoint_is_forbidden(self):
        self.client.force_login(self.pepe)
        response = self.client.get(reverse("crm_panel", args=["usuarios"]))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_hitting_the_panel_endpoint_is_forbidden(self):
        response = self.client.get(reverse("crm_panel", args=["usuarios"]))
        self.assertEqual(response.status_code, 403)

    def test_master_sees_every_account_with_origin_and_role(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("crm_panel", args=["usuarios"])).content.decode()
        for text in ("Admin", "Samuel", "pepe", "Entorno", "Maestro", "Agente", "+ Crear usuario"):
            with self.subTest(text):
                self.assertIn(text, html)

    def test_env_rows_have_no_actions_and_database_rows_do(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("crm_panel", args=["usuarios"])).content.decode()
        self.assertNotIn(f'user-dialog-{self.samuel.pk}"', html)
        self.assertIn(f'user-dialog-{self.pepe.pk}"', html)
        self.assertIn(reverse("user_set_active", args=[self.pepe.pk]), html)
        self.assertIn(reverse("user_delete", args=[self.pepe.pk]), html)

    def test_your_own_row_has_no_deactivate_or_delete(self):
        me = make_db_user("yo", name="Yo", master=True)
        self.client.force_login(me)
        html = self.client.get(reverse("crm_panel", args=["usuarios"])).content.decode()
        self.assertIn(f'user-dialog-{me.pk}"', html)  # can still rename
        self.assertNotIn(reverse("user_set_active", args=[me.pk]), html)
        self.assertNotIn(reverse("user_delete", args=[me.pk]), html)
        self.assertIn("· tú", html)

    def test_deactivated_rows_are_listed_muted_after_active_ones(self):
        make_db_user("zzz", name="Aaa", active=False)
        rows = [u.username for u in usuarios.list_users()]
        self.assertEqual(rows, ["Admin", "Samuel", "pepe", "zzz"])


# --- Mutations through the endpoints ----------------------------------------


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class UserMutationTests(TestCase):
    def setUp(self):
        self.admin, self.samuel = agents.env_users()
        self.pepe = make_db_user("pepe", name="Pepe")
        self.client.force_login(self.admin)

    def create(self, **overrides):
        data = {"username": "nuevo", "name": "Nuevo", "password": "clave-segura-1", "role": "agente"}
        data.update(overrides)
        return self.client.post(reverse("user_create"), data)

    def test_create_agent(self):
        response = self.create()
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="nuevo")
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password("clave-segura-1"))
        self.assertEqual(user.first_name, "Nuevo")
        self.assertIn("nuevo", response.content.decode())

    def test_create_master(self):
        self.create(username="jefa", role="master")
        self.assertTrue(User.objects.get(username="jefa").is_superuser)

    def test_created_users_are_not_django_staff(self):
        """Masters run the app, not /admin."""
        self.create(username="jefa", role="master")
        self.assertFalse(User.objects.get(username="jefa").is_staff)

    def test_create_rejects_env_usernames_case_insensitively(self):
        for taken in ("Samuel", "samuel", "SAMUEL"):
            with self.subTest(taken):
                response = self.create(username=taken)
                self.assertContains(response, "ya existe en el entorno")

    def test_create_rejects_existing_usernames_case_insensitively(self):
        response = self.create(username="PEPE")
        self.assertContains(response, "Ese usuario ya existe.")

    def test_create_validates_fields(self):
        cases = {
            "username": ("", "Escribe un nombre de usuario."),
            "name": ("   ", "Escribe el nombre"),
            "role": ("dios", "Elige un rol."),
        }
        for field, (value, message) in cases.items():
            with self.subTest(field):
                self.assertContains(self.create(**{field: value}), message)
        self.assertContains(self.create(username="con espacio"), "sin espacios")

    def test_password_floor(self):
        self.assertContains(self.create(password="corta"), "al menos 8")
        self.assertContains(self.create(password="12345678"), "solo números")
        self.assertContains(self.create(username="clavecita", password="ClaveCita"), "igual al usuario")

    def test_update_renames_and_promotes(self):
        self.client.post(reverse("user_update", args=[self.pepe.pk]), {"name": "Pepe Ruiz", "role": "master"})
        self.pepe.refresh_from_db()
        self.assertEqual(self.pepe.first_name, "Pepe Ruiz")
        self.assertTrue(self.pepe.is_superuser)

    def test_set_password(self):
        self.client.post(reverse("user_set_password", args=[self.pepe.pk]), {"password": "nueva-clave-9"})
        self.pepe.refresh_from_db()
        self.assertTrue(self.pepe.check_password("nueva-clave-9"))

    def test_deactivate_and_reactivate(self):
        self.client.post(reverse("user_set_active", args=[self.pepe.pk]), {"active": "0"})
        self.pepe.refresh_from_db()
        self.assertFalse(self.pepe.is_active)
        self.assertNotIn(self.pepe, agents.agent_users())

        self.client.post(reverse("user_set_active", args=[self.pepe.pk]), {"active": "1"})
        self.pepe.refresh_from_db()
        self.assertTrue(self.pepe.is_active)

    def test_delete_nulls_references_instead_of_cascading(self):
        contact = Client.objects.create(first_name="Ana", phone="+573000000001")
        conversation = Conversation.objects.create(
            contact=contact, channel="whatsapp", assigned_to=self.pepe
        )
        self.client.post(reverse("user_delete", args=[self.pepe.pk]))
        self.assertFalse(User.objects.filter(pk=self.pepe.pk).exists())
        conversation.refresh_from_db()
        self.assertIsNone(conversation.assigned_to)

    def test_unknown_user_404s(self):
        response = self.client.post(reverse("user_update", args=[9999]), {"name": "x", "role": "agente"})
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("user_create")).status_code, 405)


# --- The rules --------------------------------------------------------------


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class GuardTests(TestCase):
    def setUp(self):
        self.admin, self.samuel = agents.env_users()
        self.pepe = make_db_user("pepe", name="Pepe")

    def test_env_accounts_are_read_only(self):
        for action in (
            lambda: usuarios.update_user(self.admin, self.samuel, "Sam", "agente"),
            lambda: usuarios.set_password(self.admin, self.samuel, "nueva-clave-9"),
            lambda: usuarios.set_active(self.admin, self.samuel, False),
            lambda: usuarios.delete_user(self.admin, self.samuel),
        ):
            with self.assertRaisesMessage(usuarios.UserError, "APP_AGENTS"):
                action()
        self.samuel.refresh_from_db()
        self.assertTrue(self.samuel.is_active)

    def test_env_accounts_are_read_only_through_the_endpoints_too(self):
        self.client.force_login(self.admin)
        for name, data in (
            ("user_update", {"name": "Sam", "role": "agente"}),
            ("user_set_password", {"password": "nueva-clave-9"}),
            ("user_set_active", {"active": "0"}),
            ("user_delete", {}),
        ):
            with self.subTest(name):
                response = self.client.post(reverse(name, args=[self.samuel.pk]), data)
                self.assertContains(response, "APP_AGENTS")
        self.assertTrue(User.objects.filter(pk=self.samuel.pk).exists())

    def test_cannot_demote_deactivate_or_delete_yourself(self):
        me = make_db_user("yo", master=True)
        with self.assertRaisesMessage(usuarios.UserError, "a ti mismo"):
            usuarios.update_user(me, me, "Yo", "agente")
        with self.assertRaisesMessage(usuarios.UserError, "a ti mismo"):
            usuarios.set_active(me, me, False)
        with self.assertRaisesMessage(usuarios.UserError, "a ti mismo"):
            usuarios.delete_user(me, me)
        me.refresh_from_db()
        self.assertTrue(me.is_superuser and me.is_active)

    def test_you_can_still_rename_yourself(self):
        me = make_db_user("yo", master=True)
        usuarios.update_user(me, me, "Yo Mismo", "master")
        me.refresh_from_db()
        self.assertEqual(me.first_name, "Yo Mismo")

    @override_settings(**NO_ENV)
    def test_last_master_is_protected_when_no_env_guarantees_one(self):
        boss = make_db_user("boss", master=True)
        other = make_db_user("other", master=True)
        # With two masters, either can go.
        usuarios.update_user(boss, other, "Other", "agente")
        # Now boss is the last one -- nobody (not even another master, if
        # there were one) may strip them.
        other.refresh_from_db()
        with self.assertRaisesMessage(usuarios.UserError, "último usuario maestro"):
            usuarios.set_active(other, boss, False)
        with self.assertRaisesMessage(usuarios.UserError, "último usuario maestro"):
            usuarios.delete_user(other, boss)
        with self.assertRaisesMessage(usuarios.UserError, "último usuario maestro"):
            usuarios.update_user(other, boss, "Boss", "agente")

    def test_env_masters_count_so_the_last_database_master_may_go(self):
        boss = make_db_user("boss", master=True)
        usuarios.delete_user(self.admin, boss)
        self.assertFalse(User.objects.filter(username="boss").exists())

    def test_non_master_endpoints_are_forbidden(self):
        self.client.force_login(self.pepe)
        for name, args, data in (
            ("user_create", [], {"username": "x", "name": "X", "password": "clave-segura-1", "role": "master"}),
            ("user_update", [self.pepe.pk], {"name": "Pepe", "role": "master"}),
            ("user_set_password", [self.pepe.pk], {"password": "clave-segura-1"}),
            ("user_set_active", [self.pepe.pk], {"active": "0"}),
            ("user_delete", [self.pepe.pk], {}),
        ):
            with self.subTest(name):
                response = self.client.post(reverse(name, args=args), data)
                self.assertEqual(response.status_code, 403)
        self.pepe.refresh_from_db()
        self.assertFalse(self.pepe.is_superuser)
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_anonymous_endpoints_are_forbidden(self):
        response = self.client.post(
            reverse("user_create"),
            {"username": "x", "name": "X", "password": "clave-segura-1", "role": "master"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="x").exists())


# --- Inbox integration ------------------------------------------------------


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class InboxIntegrationTests(TestCase):
    def test_database_users_appear_in_the_assignment_dropdown(self):
        pepe = make_db_user("pepe", name="Pepe")
        contact = Client.objects.create(first_name="Ana", phone="+573000000001")
        conversation = Conversation.objects.create(
            contact=contact, channel="whatsapp", last_inbound_at=timezone.now()
        )
        html = self.client.get(reverse("inbox_chat", args=[conversation.pk])).content.decode()
        self.assertInHTML(f'<option value="{pepe.pk}">Pepe</option>', html)

    def test_deactivated_users_leave_the_dropdown(self):
        pepe = make_db_user("pepe", name="Pepe", active=False)
        contact = Client.objects.create(first_name="Ana", phone="+573000000001")
        conversation = Conversation.objects.create(contact=contact, channel="whatsapp")
        html = self.client.get(reverse("inbox_chat", args=[conversation.pk])).content.decode()
        self.assertNotIn(f'value="{pepe.pk}"', html)


# --- Findings from the design critique ---------------------------------------


@override_settings(TESTING=False, APP_AGENTS="José:clave-ñ:José", APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class NonAsciiLoginTests(TestCase):
    """hmac.compare_digest on str raises for non-ASCII; the login must not 500."""

    def test_non_ascii_env_credentials_work(self):
        response = self.client.post(reverse("login"), {"username": "José", "password": "clave-ñ"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_non_ascii_wrong_credentials_are_a_normal_rejection(self):
        response = self.client.post(reverse("login"), {"username": "José", "password": "año"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "incorrectos")

    def test_non_ascii_database_user_can_log_in(self):
        make_db_user("Ñandú", password="clave-segura-ñ")
        response = self.client.post(reverse("login"), {"username": "Ñandú", "password": "clave-segura-ñ"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class StaffAccountsAreNotAppAccountsTests(TestCase):
    """is_staff rows (createsuperuser, /admin) are invisible to the CRM: no
    login, no seat in the dropdown, no row in Usuarios, no mutation."""

    def setUp(self):
        self.admin = agents.env_users()[0]
        self.dj = User.objects.create_superuser("djadmin", password="clave-segura-1")

    @override_settings(TESTING=False)
    def test_staff_cannot_log_into_the_app(self):
        response = self.client.post(reverse("login"), {"username": "djadmin", "password": "clave-segura-1"})
        self.assertContains(response, "incorrectos")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_are_not_listed_nor_assignable(self):
        self.assertNotIn(self.dj, usuarios.list_users())
        self.assertNotIn(self.dj, agents.agent_users())

    def test_staff_rows_cannot_be_mutated_through_the_endpoints(self):
        self.client.force_login(self.admin)
        for name, data in (
            ("user_update", {"name": "X", "role": "agente"}),
            ("user_set_password", {"password": "otra-clave-segura"}),
            ("user_set_active", {"active": "0"}),
            ("user_delete", {}),
        ):
            with self.subTest(name):
                response = self.client.post(reverse(name, args=[self.dj.pk]), data)
                self.assertEqual(response.status_code, 404)
        self.dj.refresh_from_db()
        self.assertTrue(self.dj.check_password("clave-segura-1"))
        self.assertTrue(self.dj.is_active and self.dj.is_superuser)

    def test_service_layer_refuses_staff_rows_too(self):
        with self.assertRaisesMessage(usuarios.UserError, "admin de Django"):
            usuarios.set_password(self.admin, self.dj, "otra-clave-segura")

    @override_settings(**NO_ENV)
    def test_a_staff_superuser_does_not_count_as_a_master(self):
        """Otherwise the last real master could be removed, with only an
        /admin account -- which can't log into the app -- left standing."""
        boss = make_db_user("boss", master=True)
        with self.assertRaisesMessage(usuarios.UserError, "último usuario maestro"):
            usuarios.set_active(self.admin, boss, False)


@override_settings(TESTING=False, APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class SessionHygieneTests(TestCase):
    def login(self, username, password):
        self.client.post(reverse("login"), {"username": username, "password": password})

    def test_deactivating_ends_the_sessions_so_reactivating_does_not_revive_them(self):
        pepe = make_db_user("pepe", password="clave-segura-1")
        admin = agents.env_users()[0]
        self.login("pepe", "clave-segura-1")
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

        usuarios.set_active(admin, pepe, False)
        usuarios.set_active(admin, pepe, True)
        # Reactivated -- but the old browser session is gone, not revived.
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_end_sessions_only_touches_that_users_sessions(self):
        pepe = make_db_user("pepe", password="clave-segura-1")
        self.login("Samuel", "1234")
        samuel_session = self.client.session.session_key
        usuarios.end_sessions(pepe)
        from django.contrib.sessions.models import Session

        self.assertTrue(Session.objects.filter(session_key=samuel_session).exists())

    def test_changing_your_own_password_keeps_you_logged_in(self):
        me = make_db_user("yo", master=True, password="clave-segura-1")
        self.login("yo", "clave-segura-1")
        self.client.post(reverse("user_set_password", args=[me.pk]), {"password": "otra-clave-segura"})
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        me.refresh_from_db()
        self.assertTrue(me.check_password("otra-clave-segura"))

    def test_changing_someone_elses_password_signs_them_out(self):
        pepe = make_db_user("pepe", password="clave-segura-1")
        self.login("pepe", "clave-segura-1")
        pepe_client = self.client
        from django.test import Client as TestClient

        master = TestClient()
        master.post(reverse("login"), {"username": "Admin", "password": "admin-pw"})
        master.post(reverse("user_set_password", args=[pepe.pk]), {"password": "otra-clave-segura"})
        response = pepe_client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class FormErrorContractTests(TestCase):
    """A failed create keeps the dialog open and says why inside it."""

    def setUp(self):
        self.client.force_login(agents.env_users()[0])

    def test_error_response_carries_the_header_and_the_oob_message(self):
        make_db_user("pepe")
        response = self.client.post(
            reverse("user_create"),
            {"username": "pepe", "name": "Otro", "password": "clave-segura-1", "role": "agente"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Form-Error"], "1")
        html = response.content.decode()
        self.assertIn('id="user-create-error"', html)
        self.assertIn('hx-swap-oob="true"', html)
        self.assertIn("Ese usuario ya existe.", html)

    def test_success_clears_the_message_and_sends_no_header(self):
        response = self.client.post(
            reverse("user_create"),
            {"username": "nuevo", "name": "Nuevo", "password": "clave-segura-1", "role": "agente"},
        )
        self.assertFalse(response.has_header("X-Form-Error"))
        self.assertIn('id="user-create-error"', response.content.decode())
        self.assertIn("hidden", response.content.decode().split('id="user-create-error"')[1][:120])

    def test_page_renders_the_slot_hidden_once(self):
        html = self.client.get(reverse("crm_panel", args=["usuarios"])).content.decode()
        self.assertEqual(html.count('id="user-create-error"'), 1)
        self.assertNotIn('hx-swap-oob="true"', html)


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class DeleteCostTests(TestCase):
    def test_delete_dialog_states_what_is_lost(self):
        pepe = make_db_user("pepe", name="Pepe")
        contact = Client.objects.create(first_name="Ana", phone="+573000000001")
        conversation = Conversation.objects.create(contact=contact, channel="whatsapp", assigned_to=pepe)
        from messaging.models import Message

        Message.objects.create(conversation=conversation, direction="outbound", body="hola", sent_by=pepe)
        Message.objects.create(conversation=conversation, direction="outbound", body="chao", sent_by=pepe)
        self.client.force_login(agents.env_users()[0])
        html = self.client.get(reverse("crm_panel", args=["usuarios"])).content.decode()
        self.assertIn("<strong>1</strong> conversación asignada", html)
        self.assertIn("<strong>2</strong> mensajes enviados", html)

    def test_delete_dialog_for_an_untouched_user(self):
        make_db_user("pepe", name="Pepe")
        self.client.force_login(agents.env_users()[0])
        html = self.client.get(reverse("crm_panel", args=["usuarios"])).content.decode()
        self.assertIn("No tiene conversaciones asignadas ni mensajes enviados.", html)


class BootstrapCommandTests(TestCase):
    def run_command(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("crear_master", *args, stdout=out)
        return out.getvalue()

    @override_settings(**NO_ENV)
    def test_creates_a_master_that_can_log_into_the_app(self):
        out = self.run_command("jefa", "--name", "Jefa", "--password", "clave-segura-1")
        self.assertIn("jefa", out)
        user = User.objects.get(username="jefa")
        self.assertTrue(user.is_superuser and user.is_active)
        self.assertFalse(user.is_staff)
        self.assertIn(user, agents.agent_users())

    @override_settings(**NO_ENV)
    def test_repairs_an_existing_account_including_a_staff_row(self):
        User.objects.create_superuser("recup", password="x")
        self.run_command("recup", "--password", "clave-segura-1")
        user = User.objects.get(username="recup")
        self.assertFalse(user.is_staff)
        self.assertTrue(user.is_superuser and user.check_password("clave-segura-1"))

    @override_settings(APP_AGENTS=ENV)
    def test_refuses_env_usernames(self):
        from django.core.management import CommandError

        with self.assertRaisesMessage(CommandError, "APP_AGENTS"):
            self.run_command("Samuel", "--password", "clave-segura-1")

    @override_settings(**NO_ENV)
    def test_enforces_the_password_floor(self):
        from django.core.management import CommandError

        with self.assertRaisesMessage(CommandError, "al menos 8"):
            self.run_command("jefa", "--password", "corta")


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class TeamConsistencyTests(TestCase):
    def test_calendar_advisors_are_the_inbox_team(self):
        make_db_user("pepe", name="Pepe")
        make_db_user("fuera", active=False)
        User.objects.create_superuser("djadmin", password="x")
        response = self.client.get(reverse("crm_panel", args=["mi-calendario"]))
        self.assertEqual(list(response.context["advisors"]), agents.agent_users())

    def test_seed_demo_user_cannot_log_in(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_conversations", stdout=StringIO())
        asesor = User.objects.get(username="asesor")
        self.assertFalse(asesor.is_staff)
        self.assertFalse(asesor.has_usable_password())
        self.assertIsNone(agents.authenticate_user(None, "asesor", "asesor123"))


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
        """`get_hasher` has to recognise the algorithm, or it's just a password."""
        for secret in ("me$4gusta", "$$$", "sha999$abc$def"):
            with self.subTest(secret):
                self.assertFalse(agents.Agent("x", secret, "X").is_hashed)

    def test_a_32_character_password_is_not_read_as_unsalted_md5(self):
        """identify_hasher would; that's why core.agents doesn't use it."""
        secret = "a" * 32
        self.assertFalse(agents.Agent("x", secret, "X").is_hashed)

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
            mirror = agents.env_users()[0]
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

    @override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
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

    @override_settings(**NO_ENV)
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

    def test_the_name_defaults_to_the_username(self):
        entry = self.run_command("samuel", "--password", "clave-segura-1")
        self.assertTrue(entry.endswith(":samuel"))

    def test_without_a_username_it_prints_only_the_hash(self):
        encoded = self.run_command("--password", "clave-segura-1")
        self.assertTrue(agents.Agent("x", encoded, "X").is_hashed)
        self.assertNotIn(":", encoded)

    def test_it_applies_the_same_password_floor_as_the_dialog(self):
        from django.core.management import CommandError

        for bad, message in (("corta", "al menos 8"), ("12345678", "solo números")):
            with self.subTest(bad):
                with self.assertRaisesMessage(CommandError, message):
                    self.run_command("samuel", "--password", bad)

    def test_it_prompts_when_no_password_is_given(self):
        from unittest.mock import patch

        with patch("core.management.commands.hashear_clave.getpass", side_effect=["clave-segura-1"] * 2):
            entry = self.run_command("samuel")
        with override_settings(APP_AGENTS=entry, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""):
            self.assertIsNotNone(agents.authenticate("samuel", "clave-segura-1"))

    def test_a_mistyped_confirmation_is_refused(self):
        from unittest.mock import patch

        from django.core.management import CommandError

        with patch("core.management.commands.hashear_clave.getpass", side_effect=["uno", "dos"]):
            with self.assertRaisesMessage(CommandError, "no coinciden"):
                self.run_command("samuel")


class CrearMasterPromptTests(TestCase):
    @override_settings(**NO_ENV)
    def test_it_prompts_when_no_password_is_given(self):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        with patch("core.management.commands.hashear_clave.getpass", side_effect=["clave-segura-1"] * 2):
            call_command("crear_master", "jefa", stdout=StringIO())
        user = User.objects.get(username="jefa")
        self.assertTrue(user.check_password("clave-segura-1"))
        self.assertNotEqual(user.password, "clave-segura-1")

    @override_settings(**NO_ENV)
    def test_the_stored_password_is_a_hash(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("crear_master", "jefa", "--password", "clave-segura-1", stdout=StringIO())
        user = User.objects.get(username="jefa")
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))


@override_settings(APP_AGENTS=ENV, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class StoredPasswordsAreHashedTests(TestCase):
    """Nothing this app writes to the database keeps a readable password."""

    def test_a_user_created_in_the_dialog_stores_a_hash(self):
        self.client.force_login(agents.env_users()[0])
        self.client.post(
            reverse("user_create"),
            {"username": "nuevo", "name": "Nuevo", "password": "clave-segura-1", "role": "agente"},
        )
        user = User.objects.get(username="nuevo")
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertNotIn("clave-segura-1", user.password)
        self.assertTrue(user.check_password("clave-segura-1"))

    def test_a_reset_password_stores_a_hash(self):
        pepe = make_db_user("pepe")
        self.client.force_login(agents.env_users()[0])
        self.client.post(reverse("user_set_password", args=[pepe.pk]), {"password": "otra-clave-9"})
        pepe.refresh_from_db()
        self.assertTrue(pepe.password.startswith("pbkdf2_sha256$"))
        self.assertNotIn("otra-clave-9", pepe.password)

    def test_no_row_in_the_table_holds_a_readable_password(self):
        make_db_user("pepe", password="clave-segura-1")
        for user in User.objects.all():
            with self.subTest(user.username):
                self.assertFalse(user.has_usable_password() and "$" not in user.password)
