"""Create (or repair) a master account from the shell.

The recovery door. Normally the first masters come from ``APP_AGENTS`` and
every other account is created by one of them in CRM › Mi cuenta › Usuarios.
But an installation that goes database-only and then loses its last master --
a forgotten password, an over-eager delete -- has no master left to make one,
and ``createsuperuser`` won't do: it sets ``is_staff``, which the app treats as
"belongs to /admin, not the CRM" (see core.agents.can_log_in).

    python manage.py crear_master jefa --name "Jefa" --password 'una clave larga'

An existing app account with that username is promoted, reactivated and given
the password; a staff row is converted. Env usernames are refused.
"""

from django.core.management.base import BaseCommand, CommandError

from core import usuarios


class Command(BaseCommand):
    help = "Crea o repara una cuenta maestra de la app (usuario, nombre, contraseña)."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Nombre de usuario para iniciar sesión.")
        parser.add_argument("--password", required=True, help="Contraseña (mínimo 8 caracteres).")
        parser.add_argument("--name", default=None, help="Nombre visible; por defecto el usuario.")

    def handle(self, *args, username, password, name, **options):
        try:
            user = usuarios.bootstrap_master(username, name or username, password)
        except usuarios.UserError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Cuenta maestra lista: {user.username!r} ({user.first_name}). "
            f"Inicia sesión en /login/."
        ))
