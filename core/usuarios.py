"""The Usuarios page: the accounts a master manages, and the rules that keep
a team from locking itself out.

Every function here is a *master's* action (the views check that; this
module trusts ``actor`` is one) on a database account. The rules it enforces,
each raising :class:`UserError` with the sentence the UI shows:

* Env accounts (``APP_AGENTS``, see :mod:`core.agents`) are read-only --
  their source of truth is outside the app.
* You cannot demote, deactivate or delete *yourself*: the account making the
  change is the one that would stop being able to make changes.
* The last active master cannot be demoted, deactivated or deleted, unless
  the environment guarantees one -- an app with no master has no way to ever
  create one again.
* New usernames can't shadow an env agent (the env would claim the account on
  next sync, see ``agents._ensure_env_shape``) nor an existing account,
  compared case-insensitively so "samuel" and "Samuel" aren't two people.
* Django-admin staff rows (``is_staff``) are not the app's accounts at all
  (see ``agents.can_log_in``): they're neither listed nor touchable here.

The last-master rule is checked and written inside one transaction with the
live masters locked, so two masters removing each other at the same moment
can't both read "there's still another one" and both succeed.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower
from django.utils import timezone

from messaging.models import Conversation, Message

from . import agents


class UserError(ValueError):
    """A rule broken by a user-management action, worded for the UI."""


#: The two roles, as (key, label, help) for the dialog's radio cards.
ROLE_CHOICES = [
    ("agente", "Agente", "Atiende conversaciones y usa todo el CRM."),
    ("master", "Maestro", "Todo lo anterior, y además crea y gestiona usuarios."),
]

_ROLE_IS_MASTER = {"agente": False, "master": True}

#: Floor for database passwords. Env passwords are the operator's business.
MIN_PASSWORD_LENGTH = 8


def list_users() -> list:
    """Every account for the table: env agents first (in env order), then
    database accounts, active before deactivated, by name.

    Each row carries ``managed_by_env`` so the template can render the badge
    and hide the actions without re-deriving it per cell, and database rows
    carry ``assigned_count`` / ``sent_count`` so the delete confirmation can
    say what deleting them costs (two GROUP BY queries, not a join per row).
    """
    env = agents.env_users()
    for user in env:
        user.managed_by_env = True

    User = get_user_model()
    others = list(
        User.objects.filter(is_staff=False)
        .exclude(username__in=[user.username for user in env])
        .order_by("-is_active", Lower("first_name"), "username")
    )
    assigned = _count_by_user(Conversation.objects.all(), "assigned_to")
    sent = _count_by_user(Message.objects.all(), "sent_by")
    for user in others:
        user.managed_by_env = False
        user.assigned_count = assigned.get(user.pk, 0)
        user.sent_count = sent.get(user.pk, 0)
    return env + others


def _count_by_user(queryset, field: str) -> dict[int, int]:
    rows = queryset.values(field).annotate(n=Count("pk")).order_by()
    return {row[field]: row["n"] for row in rows if row[field] is not None}


def bootstrap_master(username: str, name: str, password: str):
    """Create -- or repair -- a master from the shell (``manage.py
    crear_master``): the door for when there is no master left to open the
    UI's, and ``APP_AGENTS`` isn't set.

    An existing app account with that username is promoted, reactivated and
    given the password; a staff row is converted (``is_staff`` cleared) since
    the whole point is a CRM login, which staff rows can't be. Env usernames
    are refused: the env is their door.
    """
    username = (username or "").strip()
    if username.casefold() in {n.casefold() for n in agents.env_usernames()}:
        raise UserError("Ese usuario ya existe en el entorno (APP_AGENTS).")
    User = get_user_model()
    user = User.objects.filter(username__iexact=username).first()
    if user is None:
        return create_user(None, username, name, password, "master")
    name = _clean_name(name or user.first_name or user.username)
    validate_password(password, user.username)
    user.first_name = name
    user.is_superuser = True
    user.is_active = True
    user.is_staff = False
    user.set_password(password)
    user.save()
    return user


def create_user(actor, username: str, name: str, password: str, role: str):
    """Create a database account. Returns the new ``User``."""
    username = _clean_username(username)
    name = _clean_name(name)
    is_master = _role_to_master(role)
    validate_password(password, username)

    User = get_user_model()
    user = User(username=username, first_name=name, is_superuser=is_master)
    user.set_password(password)
    user.save()
    return user


def update_user(actor, user, name: str, role: str):
    """Rename and/or change the role of a database account."""
    _guard_app_managed(user)
    name = _clean_name(name)
    is_master = _role_to_master(role)

    with transaction.atomic():
        if user.is_superuser and not is_master:
            if user.pk == actor.pk:
                raise UserError("No puedes quitarte a ti mismo el rol de maestro.")
            _guard_last_master(user, "quitar el rol de maestro a")

        user.first_name = name
        user.is_superuser = is_master
        user.save(update_fields=["first_name", "is_superuser"])
    return user


def set_password(actor, user, password: str) -> None:
    """Set a new password on a database account.

    Django's session auth hash changes with the password, so the person is
    signed out on their next request and comes back in with the new one.
    """
    _guard_app_managed(user)
    validate_password(password, user.username)
    user.set_password(password)
    user.save(update_fields=["password"])


def set_active(actor, user, active: bool) -> None:
    """Deactivate (can't log in, leaves the assignment dropdown, keeps their
    name on history) or reactivate a database account.

    Deactivating also ends their sessions: otherwise the session row sits
    there, AuthenticationMiddleware just stops resolving it, and a later
    reactivation would silently revive a browser nobody logged into again.
    """
    _guard_app_managed(user)
    with transaction.atomic():
        if not active:
            if user.pk == actor.pk:
                raise UserError("No puedes desactivarte a ti mismo.")
            _guard_last_master(user, "desactivar a")
        if user.is_active != active:
            user.is_active = active
            user.save(update_fields=["is_active"])
        if not active:
            end_sessions(user)


def delete_user(actor, user) -> None:
    """Hard-delete a database account.

    Everything that pointed at them -- conversations, messages, tags,
    calendar events -- stays, with the reference nulled (every FK to User is
    ``SET_NULL``). Deactivating is the reversible option; this one is here
    because it was asked for, and the confirm dialog says what it costs.
    """
    _guard_app_managed(user)
    with transaction.atomic():
        if user.pk == actor.pk:
            raise UserError("No puedes eliminarte a ti mismo.")
        _guard_last_master(user, "eliminar a")
        end_sessions(user)
        user.delete()


def end_sessions(user) -> None:
    """Delete every live session belonging to ``user``.

    Sessions don't reference users by FK -- the id is inside the signed
    payload -- so this decodes each unexpired row. The table is small here
    (one row per logged-in browser), and it's only done on deactivate/delete.
    """
    wanted = str(user.pk)
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == wanted:
            session.delete()


# --- Rules ------------------------------------------------------------------


def _guard_app_managed(user) -> None:
    if agents.is_env_managed(user):
        raise UserError(
            "Este usuario se gestiona desde el entorno (APP_AGENTS), no desde la app."
        )
    if user.is_staff:
        raise UserError(
            "Esa cuenta pertenece al admin de Django (/admin), no a la app."
        )


def _guard_last_master(user, verb: str) -> None:
    """Refuse to strip the last master who can actually log in, unless the
    environment guarantees one -- env agents are always masters and can't be
    touched here, so with any configured the app can never run out.

    "Can actually log in" is the point: a ghost mirror (an agent removed from
    ``APP_AGENTS``, still a master on paper but with no password) would
    otherwise count, and the team would discover the difference locked out.
    """
    if not (user.is_superuser and user.is_active):
        return
    if agents.configured_agents():
        return
    # Lock the live masters (callers are inside transaction.atomic) so a
    # concurrent removal of another master waits for this one to commit and
    # then sees the true count, instead of both reading "one more" and both
    # going through. select_for_update is a no-op on SQLite and real on
    # Postgres, which is where it matters.
    User = get_user_model()
    live = list(
        agents.can_log_in(User.objects.select_for_update().filter(is_superuser=True))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    if not any(pk != user.pk for pk in live):
        raise UserError(f"No puedes {verb} el último usuario maestro.")


def _clean_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise UserError("Escribe un nombre de usuario.")
    if len(username) > 150:
        raise UserError("El nombre de usuario es demasiado largo.")
    try:
        UnicodeUsernameValidator()(username)
    except ValidationError:
        raise UserError(
            "El usuario solo puede tener letras, números y @ . + - _ (sin espacios)."
        )
    if username.casefold() in {name.casefold() for name in agents.env_usernames()}:
        raise UserError("Ese usuario ya existe en el entorno (APP_AGENTS).")
    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        raise UserError("Ese usuario ya existe.")
    return username


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise UserError("Escribe el nombre que verán los demás.")
    if len(name) > 150:
        raise UserError("El nombre es demasiado largo.")
    return name


def _role_to_master(role: str) -> bool:
    try:
        return _ROLE_IS_MASTER[role]
    except KeyError:
        raise UserError("Elige un rol.")


def validate_password(password: str, username: str) -> None:
    """A small, Spanish-worded floor -- the project's AUTH_PASSWORD_VALIDATORS
    would say the same things in English, in an all-Spanish UI.

    Public because the management commands apply the same rule: a password
    reaching APP_AGENTS as a hash should clear the same bar as one typed into
    the Usuarios dialog."""
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    if password.isdigit():
        raise UserError("La contraseña no puede ser solo números.")
    if password.casefold() == username.casefold():
        raise UserError("La contraseña no puede ser igual al usuario.")
