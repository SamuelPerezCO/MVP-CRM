"""Turn a password into the hash that goes in ``APP_AGENTS``.

``APP_AGENTS`` entries carry a password *hash*, so the environment never holds
a working credential (see core/agents.py). This is what produces one:

    python manage.py hashear_clave Samuel --name Samuel

It prompts for the password (twice, hidden) unless ``--password`` is given,
and prints the whole ``usuario:hash:Nombre`` entry ready to paste. With no
username it prints just the hash.

Name every agent at once and it prints the finished variable instead::

    python manage.py hashear_clave Admin Samuel
    APP_AGENTS=Admin:pbkdf2_sha256$...:Admin,Samuel:pbkdf2_sha256$...:Samuel

which is the whole point: joining the entries by hand is where a stray comma
locks the team out of a deploy nobody can log into to fix.

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
            "usernames",
            nargs="*",
            metavar="usuario",
            help="Con uno, imprime la entrada usuario:hash:Nombre. Con varios, "
                 "la línea APP_AGENTS= completa. Sin ninguno, solo el hash.",
        )
        parser.add_argument(
            "--name",
            default=None,
            help="Nombre visible; por defecto el usuario. Solo con un usuario.",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Contraseña. Si la omites se pide por teclado, que es lo recomendable: "
                 "así no queda en el historial del shell. Solo con un usuario.",
        )

    def handle(self, *args, usernames, name, password, **options):
        if len(usernames) > 1 and (name or password):
            raise CommandError(
                "--name y --password son para un solo usuario; con varios se pide "
                "cada contraseña por teclado y el nombre visible es el usuario."
            )

        if len(usernames) > 1:
            entries = [
                self._entry(user, user, ask_password(f"Contraseña de {user}: "))
                for user in usernames
            ]
            self.stdout.write("APP_AGENTS=" + ",".join(entries))
            return

        username = usernames[0] if usernames else ""
        if password is None:
            password = ask_password()
        encoded = self._hash(password, username)
        self.stdout.write(
            f"{username}:{encoded}:{name or username}" if username else encoded
        )

    def _entry(self, username, display_name, password) -> str:
        return f"{username}:{self._hash(password, username)}:{display_name}"

    def _hash(self, password: str, username: str) -> str:
        try:
            agents.validate_password(password, username)
        except ValueError as exc:
            raise CommandError(f"{username or 'la contraseña'}: {exc}" if username else str(exc))

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
        return encoded


def ask_password(prompt: str = "Contraseña: ") -> str:
    """Prompt twice, hidden, and refuse a mismatch."""
    first = getpass(prompt)
    second = getpass("Repítela: ")
    if first != second:
        raise CommandError("Las contraseñas no coinciden.")
    return first
