"""Turn a password into the hash that goes in ``APP_AGENTS``.

``APP_AGENTS`` entries carry a password *hash*, so the environment never holds
a working credential (see core/agents.py). This is what produces one:

    python manage.py hashear_clave Samuel --name Samuel

It prompts for the password (twice, hidden) unless ``--password`` is given,
and prints the whole ``usuario:hash:Nombre`` entry ready to paste. With no
username it prints just the hash.

The hash uses the project's default hasher, so it is verified by exactly the
same code path as a database account's password.
"""

from getpass import getpass

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from core import agents


class Command(BaseCommand):
    help = "Genera el hash de una contraseña para pegarlo en APP_AGENTS."

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            nargs="?",
            help="Si lo indicas, imprime la entrada completa usuario:hash:Nombre.",
        )
        parser.add_argument("--name", default=None, help="Nombre visible; por defecto el usuario.")
        parser.add_argument(
            "--password",
            default=None,
            help="Contraseña. Si la omites se pide por teclado, que es lo recomendable: "
                 "así no queda en el historial del shell.",
        )

    def handle(self, *args, username, name, password, **options):
        if password is None:
            password = ask_password(self.stderr)

        try:
            agents.validate_password(password, username or "")
        except agents.WeakPassword as exc:
            raise CommandError(str(exc))

        encoded = make_password(password)
        # The parser splits APP_AGENTS on these, so a hash containing one
        # would silently truncate. Django's default PBKDF2 never does; Argon2
        # puts commas in its parameters, hence the guard rather than trust.
        if ":" in encoded or "," in encoded:
            raise CommandError(
                "El hasher configurado produce un hash con ':' o ',', que son los "
                "separadores de APP_AGENTS. Usa PBKDF2 (el de Django por defecto) "
                "para las cuentas del entorno."
            )

        if username:
            self.stdout.write(f"{username}:{encoded}:{name or username}")
        else:
            self.stdout.write(encoded)


def ask_password(stream) -> str:
    """Prompt twice, hidden, and refuse a mismatch."""
    first = getpass("Contraseña: ")
    second = getpass("Repite la contraseña: ")
    if first != second:
        raise CommandError("Las contraseñas no coinciden.")
    return first
