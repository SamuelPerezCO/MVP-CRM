"""Refuse to run a development-only command against a real database.

``seed_conversations`` and ``simulate_inbound`` exist to fill an empty CRM
with believable fixtures. Both are one ``python manage.py ...`` away from
writing that fiction into production, because ``.env`` carries the production
``DATABASE_URL`` and ``config.settings`` reads it for *every* command, not
just ``runserver``. The Inbox would then show invented customers next to real
ones, which is exactly the mess this project is trying to leave behind.

The rule is the database engine rather than ``DEBUG``: production is Postgres
and local development is a SQLite file, so "is this SQLite?" is the question
with no false negatives -- a developer who exports the production URL still
gets stopped, even with ``DEBUG=True``.

``ALLOW_DEV_COMMANDS_ON_REMOTE_DB=1`` overrides it, for the rare case of
seeding a throwaway staging Postgres. Deliberately an environment variable
and not a ``--force`` flag: it should take a moment's thought, not a
tab-completion.
"""

from __future__ import annotations

import os

from django.core.management.base import CommandError
from django.db import connection

OVERRIDE_ENV_VAR = "ALLOW_DEV_COMMANDS_ON_REMOTE_DB"


def require_local_database(command_name: str) -> None:
    """Raise ``CommandError`` unless the active database is a local SQLite file."""
    if os.environ.get(OVERRIDE_ENV_VAR) == "1":
        return

    db = connection.settings_dict
    engine = db.get("ENGINE", "")
    if engine.endswith("sqlite3"):
        return

    target = db.get("HOST") or db.get("NAME") or "?"
    raise CommandError(
        f"`{command_name}` solo puede correr contra la base local (SQLite).\n"
        f"La base activa es {engine.split('.')[-1]} en {target} -- probablemente "
        f"la de producción, porque .env define DATABASE_URL.\n\n"
        f"Para la base local, corre el comando sin DATABASE_URL:\n"
        f"    DATABASE_URL= python manage.py {command_name} ...\n\n"
        f"Si de verdad quieres escribir datos de prueba en esa base remota:\n"
        f"    {OVERRIDE_ENV_VAR}=1 python manage.py {command_name} ..."
    )
