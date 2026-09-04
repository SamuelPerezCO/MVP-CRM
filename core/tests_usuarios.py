"""Tests for CRM > Equipo > Usuarios: app-created users next to the env
agents, the master rule, and the login/assignment paths they plug into."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core import agents
from core.middleware import SESSION_KEY
from core.models import Client
from messaging.models import Conversation

User = get_user_model()
TWO_AGENTS = "Admin:admin-pw:Admin,Samuel:1234:Samuel"
PAGE = reverse("section", args=["crm"]) + "?view=usuarios"


def app_user(username="lucia", password="clave-larga", master=False, name="Lucía"):
    return agents.create_user(username, password, name, master)


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class AgentsWithDbUsersTests(TestCase):
    def test_django_staff_is_not_a_crm_master(self):
        """is_staff means "may open /admin/", not "may manage this team" --
        the old generator marked its demo advisor staff, and that must not
        hand them the Usuarios page."""
        demo = User.objects.create_user("asesor", password="asesor123")
        demo.is_staff = True
        demo.save()
        self.assertFalse(agents.is_master(demo))
        # A superuser is a master: that account can do anything anyway.
        root = User.objects.create_superuser("root", password="clave-larga")
        self.assertTrue(agents.is_master(root))

    def test_promoting_puts_the_user_in_the_maestros_group(self):
        lucia = app_user()
        agents.update_user(lucia, "Lucía", True)
        self.assertTrue(lucia.groups.filter(name=agents.MASTER_GROUP).exists())
        self.assertFalse(lucia.is_staff)   # /admin stays shut
        agents.update_user(lucia, "Lucía", False)
        self.assertFalse(agents.is_master(lucia))

    def test_env_agents_are_masters_and_app_users_are_not_by_default(self):
        admin = agents.authenticate("Admin", "admin-pw").user
        lucia = app_user()
        self.assertTrue(agents.is_master(admin))
        self.assertFalse(agents.is_master(lucia))
        self.assertTrue(agents.is_master(app_user("jefe", master=True)))

    def test_app_users_can_log_in_through_the_same_authenticate(self):
        app_user()
        agent = agents.authenticate("lucia", "clave-larga")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.user.username, "lucia")
        self.assertEqual(agent.display_name, "Lucía")
        self.assertIsNone(agents.authenticate("lucia", "otra"))

    def test_env_usernames_only_log_in_with_the_env_password(self):
        # The mirror row has an unusable password; the DB step can't be
        # used to sneak past the env list.
        mirror = agents.authenticate("Admin", "admin-pw").user
        mirror.set_password("db-pw")
        mirror.save()
        self.assertIsNone(agents.authenticate("Admin", "db-pw"))
        self.assertIsNotNone(agents.authenticate("Admin", "admin-pw"))

    def test_deactivated_users_cannot_log_in(self):
        lucia = app_user()
        agents.set_user_active(lucia, False)
        self.assertIsNone(agents.authenticate("lucia", "clave-larga"))

    def test_agent_users_lists_env_first_then_app_users(self):
        app_user("zoe", name="Zoe")
        app_user("ana", name="Ana")
        User.objects.create(username="seed-no-password")   # no usable password
        off = app_user("off", name="Off")
        agents.set_user_active(off, False)
        names = [user.username for user in agents.agent_users()]
        self.assertEqual(names, ["Admin", "Samuel", "ana", "zoe"])

    def test_create_user_refuses_taken_and_env_usernames(self):
        app_user()
        with self.assertRaises(agents.UsernameTaken):
            app_user("Lucia")           # case-insensitive
        with self.assertRaises(agents.UsernameTaken):
            app_user("Samuel")          # env name, even before its mirror exists

    def test_env_mirrors_cannot_be_edited_or_deactivated_here(self):
        admin = agents.authenticate("Admin", "admin-pw").user
        with self.assertRaises(ValueError):
            agents.update_user(admin, "Otro", False)
        with self.assertRaises(ValueError):
            agents.set_user_active(admin, False)

    def test_update_user_can_reset_the_password(self):
        lucia = app_user()
        agents.update_user(lucia, "Lucía R.", True, "nueva-clave")
        self.assertIsNotNone(agents.authenticate("lucia", "nueva-clave"))
        self.assertIsNone(agents.authenticate("lucia", "clave-larga"))
        self.assertTrue(agents.is_master(lucia))


@override_settings(
    TESTING=False, APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD=""
)
class AppUserLoginTests(TestCase):
    """The real gate, with the DB user going through the login form."""

    def test_an_app_created_user_gets_a_real_session(self):
        app_user()
        response = self.client.post(
            reverse("login"), {"username": "lucia", "password": "clave-larga"}
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertTrue(self.client.session.get(SESSION_KEY))
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get(username="lucia").pk)

    def test_a_deactivated_user_is_turned_away(self):
        lucia = app_user()
        agents.set_user_active(lucia, False)
        response = self.client.post(
            reverse("login"), {"username": "lucia", "password": "clave-larga"}
        )
        self.assertContains(response, "incorrectos")


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class UsuariosPageTests(TestCase):
    def login_as(self, username, password):
        # TESTING keeps the gate open; force_login sets request.user, which
        # is what the master check reads.
        self.client.force_login(agents.authenticate(username, password).user)

    def test_the_nav_has_the_equipo_section(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn("Equipo", html)
        self.assertIn("?view=usuarios", html)

    def test_a_staff_only_user_gets_the_page_read_only(self):
        demo = User.objects.create_user("asesor", password="asesor123")
        demo.is_staff = True
        demo.save()
        self.client.force_login(demo)
        html = self.client.get(PAGE).content.decode()
        self.assertIn("Solo un usuario maestro", html)
        self.assertNotIn("+ Crear usuario", html)

    def test_masters_see_the_create_button_and_the_env_rows(self):
        self.login_as("Admin", "admin-pw")
        html = self.client.get(PAGE).content.decode()
        self.assertIn("+ Crear usuario", html)
        self.assertIn("Entorno (APP_AGENTS)", html)
        self.assertIn("Maestro", html)
        self.assertNotIn("Solo un usuario maestro", html)

    def test_agents_get_the_list_read_only(self):
        app_user()
        self.client.force_login(User.objects.get(username="lucia"))
        html = self.client.get(PAGE).content.decode()
        self.assertNotIn("+ Crear usuario", html)
        self.assertIn("Solo un usuario maestro", html)
        self.assertIn("Lucía", html)

    def test_a_master_creates_a_user_who_can_then_log_in(self):
        self.login_as("Admin", "admin-pw")
        response = self.client.post(
            reverse("usuario_create"),
            {"username": "pedro", "display_name": "Pedro", "password": "clave-larga",
             "password2": "clave-larga"},
        )
        self.assertContains(response, "data-dialog-dismiss")
        self.assertContains(response, "Pedro")
        self.assertIsNotNone(agents.authenticate("pedro", "clave-larga"))
        self.assertFalse(agents.is_master(User.objects.get(username="pedro")))

    def test_create_validates(self):
        self.login_as("Admin", "admin-pw")
        html = self.client.post(
            reverse("usuario_create"),
            {"username": "con espacio", "password": "corta", "password2": "otra"},
        ).content.decode()
        self.assertIn("Sin espacios", html)
        self.assertIn("Mínimo 8", html)
        self.assertNotIn("data-dialog-dismiss", html)
        self.assertEqual(User.objects.filter(username="con espacio").count(), 0)

    def test_mismatched_passwords_are_refused(self):
        self.login_as("Admin", "admin-pw")
        html = self.client.post(
            reverse("usuario_create"),
            {"username": "pedro", "password": "clave-larga", "password2": "clave-largo"},
        ).content.decode()
        self.assertIn("no coinciden", html)

    def test_a_taken_username_is_refused_by_name(self):
        self.login_as("Admin", "admin-pw")
        app_user()
        html = self.client.post(
            reverse("usuario_create"),
            {"username": "lucia", "password": "clave-larga", "password2": "clave-larga"},
        ).content.decode()
        self.assertIn("Ya existe un usuario", html)

    def test_edit_renames_promotes_and_resets_the_password(self):
        self.login_as("Admin", "admin-pw")
        lucia = app_user()
        html = self.client.get(reverse("usuario_update", args=[lucia.pk])).content.decode()
        self.assertIn('value="lucia"', html)
        self.assertIn("readonly", html)
        self.client.post(
            reverse("usuario_update", args=[lucia.pk]),
            {"display_name": "Lucía Rojas", "master": "1", "password": "nueva-clave",
             "password2": "nueva-clave"},
        )
        lucia.refresh_from_db()
        self.assertEqual(lucia.first_name, "Lucía Rojas")
        self.assertTrue(agents.is_master(lucia))
        self.assertIsNotNone(agents.authenticate("lucia", "nueva-clave"))

    def test_edit_with_blank_password_keeps_the_old_one(self):
        self.login_as("Admin", "admin-pw")
        lucia = app_user()
        self.client.post(
            reverse("usuario_update", args=[lucia.pk]), {"display_name": "Lu", "password": ""}
        )
        self.assertIsNotNone(agents.authenticate("lucia", "clave-larga"))

    def test_a_master_cannot_demote_themselves(self):
        jefe = app_user("jefe", master=True)
        self.client.force_login(jefe)
        self.client.post(reverse("usuario_update", args=[jefe.pk]), {"display_name": "Jefe"})
        jefe.refresh_from_db()
        self.assertTrue(agents.is_master(jefe))

    def test_deactivate_and_restore(self):
        self.login_as("Admin", "admin-pw")
        lucia = app_user()
        response = self.client.post(reverse("usuario_active", args=[lucia.pk]), {"active": "0"})
        self.assertContains(response, "Usuario desactivado")
        lucia.refresh_from_db()
        self.assertFalse(lucia.is_active)
        self.assertNotIn(lucia, agents.agent_users())
        self.client.post(reverse("usuario_active", args=[lucia.pk]), {"active": "1"})
        lucia.refresh_from_db()
        self.assertTrue(lucia.is_active)

    def test_a_master_cannot_deactivate_themselves(self):
        jefe = app_user("jefe", master=True)
        self.client.force_login(jefe)
        response = self.client.post(reverse("usuario_active", args=[jefe.pk]), {"active": "0"})
        self.assertContains(response, "tu propio usuario")
        jefe.refresh_from_db()
        self.assertTrue(jefe.is_active)

    def test_env_agents_cannot_be_edited_from_the_page(self):
        self.login_as("Admin", "admin-pw")
        samuel = agents.authenticate("Samuel", "1234").user
        response = self.client.get(reverse("usuario_update", args=[samuel.pk]))
        self.assertContains(response, "se configura en el entorno")
        response = self.client.post(reverse("usuario_active", args=[samuel.pk]), {"active": "0"})
        self.assertContains(response, "se configura en el entorno")
        samuel.refresh_from_db()
        self.assertTrue(samuel.is_active)

    def test_non_masters_are_refused_with_403(self):
        lucia = app_user()
        self.client.force_login(lucia)
        for method, url, data in (
            ("get", reverse("usuario_create"), {}),
            ("post", reverse("usuario_create"), {"username": "x", "password": "clave-larga", "password2": "clave-larga"}),
            ("post", reverse("usuario_active", args=[lucia.pk]), {"active": "0"}),
        ):
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, data)
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, "Sin permiso", status_code=403)
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_deactivated_users_stay_listed_for_restoring(self):
        self.login_as("Admin", "admin-pw")
        lucia = app_user()
        agents.set_user_active(lucia, False)
        html = self.client.get(PAGE).content.decode()
        self.assertIn("Desactivado", html)
        self.assertIn(f'aria-label="Restaurar lucia"', html)


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class AssignmentIncludesAppUsersTests(TestCase):
    def test_an_app_user_shows_up_in_the_assignment_dropdown(self):
        lucia = app_user()
        contact = Client.objects.create(first_name="Camila", phone="+571")
        conversation = Conversation.objects.create(contact=contact, channel="whatsapp")
        options = agents.assignment_options(conversation)
        self.assertIn(lucia, options)
        html = self.client.get(reverse("inbox_chat", args=[conversation.pk])).content.decode()
        self.assertIn("Lucía", html)


# --- Guards ported from the agent-assignment branch --------------------------

NO_ENV = dict(APP_AGENTS="", APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")


@override_settings(**NO_ENV)
class LastMasterTests(TestCase):
    """With no env agent to fall back on, the team must keep one master who
    can actually log in -- otherwise nobody can ever manage users again."""

    def test_the_only_master_cannot_be_demoted(self):
        jefa = app_user("jefa", master=True)
        with self.assertRaisesMessage(agents.LastMaster, "último usuario maestro"):
            agents.update_user(jefa, "Jefa", False)
        jefa.refresh_from_db()
        self.assertTrue(agents.is_master(jefa))

    def test_the_only_master_cannot_be_deactivated(self):
        jefa = app_user("jefa", master=True)
        with self.assertRaisesMessage(agents.LastMaster, "último usuario maestro"):
            agents.set_user_active(jefa, False)
        jefa.refresh_from_db()
        self.assertTrue(jefa.is_active)

    def test_with_two_masters_either_may_go(self):
        jefa = app_user("jefa", master=True)
        app_user("otro", master=True)
        agents.update_user(jefa, "Jefa", False)
        self.assertFalse(agents.is_master(User.objects.get(username="jefa")))

    def test_a_deactivated_master_does_not_count_as_the_other_one(self):
        jefa = app_user("jefa", master=True)
        dormido = app_user("dormido", master=True)
        agents.set_user_active(dormido, False)
        with self.assertRaises(agents.LastMaster):
            agents.set_user_active(jefa, False)

    def test_a_superuser_counts_because_they_can_log_in(self):
        jefa = app_user("jefa", master=True)
        User.objects.create_superuser("root", password="clave-larga")
        agents.set_user_active(jefa, False)
        jefa.refresh_from_db()
        self.assertFalse(jefa.is_active)

    def test_a_master_with_no_usable_password_does_not_count(self):
        """A mirror left behind by an agent dropped from APP_AGENTS is a
        master on paper with no way in -- not a stand-in for a real one."""
        jefa = app_user("jefa", master=True)
        fantasma = app_user("fantasma", master=True)
        fantasma.set_unusable_password()
        fantasma.save(update_fields=["password"])
        with self.assertRaises(agents.LastMaster):
            agents.set_user_active(jefa, False)

    def test_promoting_and_renaming_are_never_blocked(self):
        jefa = app_user("jefa", master=True)
        agents.update_user(jefa, "Jefa Nueva", True)
        jefa.refresh_from_db()
        self.assertEqual(jefa.first_name, "Jefa Nueva")

    def test_demoting_a_non_master_is_not_blocked(self):
        app_user("jefa", master=True)
        lucia = app_user("lucia")
        agents.update_user(lucia, "Lucía", False)
        self.assertFalse(agents.is_master(User.objects.get(username="lucia")))


@override_settings(APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class EnvGuaranteesAMasterTests(TestCase):
    def test_the_last_db_master_may_go_when_the_env_supplies_one(self):
        """APP_AGENTS entries are always masters and always able to log in,
        so the set can never empty while one is configured."""
        jefa = app_user("jefa", master=True)
        agents.update_user(jefa, "Jefa", False)
        self.assertFalse(agents.is_master(User.objects.get(username="jefa")))

    def test_and_deactivated_too(self):
        jefa = app_user("jefa", master=True)
        agents.set_user_active(jefa, False)
        jefa.refresh_from_db()
        self.assertFalse(jefa.is_active)


@override_settings(TESTING=False, APP_AGENTS=TWO_AGENTS, APP_LOGIN_USERNAME="", APP_LOGIN_PASSWORD="")
class DeactivationEndsSessionsTests(TestCase):
    def login(self, username, password):
        return self.client.post(
            reverse("login"), {"username": username, "password": password}
        )

    def test_deactivating_ends_the_session_so_restoring_does_not_revive_it(self):
        lucia = app_user("lucia", password="clave-larga")
        self.login("lucia", "clave-larga")
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

        agents.set_user_active(lucia, False)
        agents.set_user_active(lucia, True)

        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_the_row_is_actually_gone_not_just_unresolvable(self):
        from django.contrib.sessions.models import Session

        lucia = app_user("lucia", password="clave-larga")
        self.login("lucia", "clave-larga")
        key = self.client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=key).exists())

        agents.set_user_active(lucia, False)
        self.assertFalse(Session.objects.filter(session_key=key).exists())

    def test_only_that_users_sessions_go(self):
        from django.contrib.sessions.models import Session

        lucia = app_user("lucia", password="clave-larga")
        self.login("Samuel", "1234")
        samuel_key = self.client.session.session_key

        agents.set_user_active(lucia, False)
        self.assertTrue(Session.objects.filter(session_key=samuel_key).exists())

    def test_restoring_does_not_touch_sessions(self):
        lucia = app_user("lucia", password="clave-larga")
        agents.set_user_active(lucia, False)
        agents.set_user_active(lucia, True)
        self.assertIsNotNone(agents.authenticate("lucia", "clave-larga"))


@override_settings(**NO_ENV)
class LastMasterAndTheViewsTests(TestCase):
    """Where the guard sits relative to the screen.

    Through the UI it is belt-and-braces: a non-master gets 403, the view
    already refuses self-demotion and self-deactivation, and whoever is left
    doing the deactivating is themselves a master who can log in -- so the
    set never empties by that route. The guard exists for everything that is
    not the form: a management command, a shell, a future caller.
    """

    def setUp(self):
        self.jefa = app_user("jefa", master=True)
        self.otro = app_user("otro", master=True)
        self.client.force_login(self.jefa)

    def test_a_master_may_deactivate_another_because_they_remain(self):
        response = self.client.post(
            reverse("usuario_active", args=[self.otro.pk]), {"active": "0"}
        )
        self.assertEqual(response.status_code, 200)
        self.otro.refresh_from_db()
        self.assertFalse(self.otro.is_active)
        self.assertTrue(agents.is_master(self.jefa))

    def test_and_that_deactivation_ended_their_session(self):
        self.client.post(reverse("usuario_active", args=[self.otro.pk]), {"active": "0"})
        # Nothing to resolve the old session to any more.
        self.otro.refresh_from_db()
        self.assertFalse(self.otro.is_active)

    def test_the_service_still_refuses_what_the_form_cannot_ask(self):
        """Deactivate the other master through the view, then try to take the
        last one out from under the app the way a script would."""
        self.client.post(reverse("usuario_active", args=[self.otro.pk]), {"active": "0"})
        with self.assertRaisesMessage(agents.LastMaster, "último usuario maestro"):
            agents.set_user_active(self.jefa, False)
        with self.assertRaisesMessage(agents.LastMaster, "último usuario maestro"):
            agents.update_user(self.jefa, "Jefa", False)

    def test_the_form_renders_a_master_error_when_there_is_one(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            "partials/crm/usuarios/form.html",
            {
                "form": {"username": "jefa", "display_name": "Jefa", "master": True},
                "errors": {"master": "No puedes quitarle el rol de maestro al último usuario maestro que puede entrar."},
                "edit_user": self.otro,
            },
        )
        self.assertIn('id="user-error-master"', html)
        self.assertIn("último usuario maestro", html)
