"""The people who log in and answer conversations -- "agentes" in the UI.

One identity model (Django's ``User``) fed from two sources:

* **The environment** -- ``APP_AGENTS``. These are the founding accounts: the
  ones that exist before anyone has logged in, so the team can never be
  locked out by a database it can't reach. They are always masters, always
  active, and read-only inside the app, because the environment is their
  source of truth: nothing in the UI can rename, demote, deactivate or delete
  them, and a password change means editing the variable and redeploying.

* **The database** -- accounts a master creates in CRM › Mi cuenta ›
  Usuarios (:mod:`core.usuarios`). Real hashed passwords, a chosen role, and
  the full lifecycle: rename, promote/demote, new password, deactivate,
  delete.

Both kinds end up as ``User`` rows -- ``Conversation.assigned_to``,
``Message.sent_by`` and friends are FKs to ``AUTH_USER_MODEL`` -- but only
database accounts can authenticate *through* the row. Env accounts get a
mirror row with an unusable password: it exists to be pointed at, never to
log in with, so ``APP_AGENTS`` stays the only way those credentials work.

**Role** is ``User.is_superuser``: a *master* can do everything, including
managing users; an *agent* can do everything except that. Deliberately not
``is_staff`` -- masters are not Django-admin staff, and /admin stays closed to
them.

``APP_AGENTS`` format -- comma-separated ``username:hash:Nombre`` entries::

    APP_AGENTS=Admin:pbkdf2_sha256$1500000$XbY...$vTh...=:Admin

The middle field is a **password hash**, not a password: generate one with
``manage.py hashear_clave`` and paste it in. Verification goes through
``django.contrib.auth.hashers.check_password``, the same function and the same
PBKDF2 cost as a database account, so whoever can read the environment (a
Vercel dashboard, a CI log, a shared .env) sees a hash rather than a working
credential. The display name is optional (``username:hash`` falls back to the
username). Colons and commas can't appear in the middle field, since they are
the separators -- Django's default PBKDF2 hashes contain neither. (Argon2 does
put commas in its parameters; it is not installed here, and
``hashear_clave`` refuses to emit anything the parser would split.)

A **plaintext** password is still accepted there, so an environment written
before this existed keeps working, but it is deprecated: ``manage.py check``
warns for every agent still configured that way (see :mod:`core.checks`).

If ``APP_AGENTS`` is unset the older single pair
(``APP_LOGIN_USERNAME``/``APP_LOGIN_PASSWORD``) is used as a one-agent list --
that password may equally be a hash.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import authenticate as django_authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import (
    UNUSABLE_PASSWORD_PREFIX,
    check_password,
    get_hasher,
    make_password,
)
from django.db.models.functions import Lower


@dataclass(frozen=True)
class Agent:
    """One configured agent, straight from the environment."""

    username: str

    secret: str
    """This agent's password *hash* -- or, deprecated, a raw password.

    Named for what it holds rather than what it is: :meth:`accepts` is what
    knows the difference, and nothing else should have to.
    """

    display_name: str

    @property
    def is_hashed(self) -> bool:
        """Whether :attr:`secret` is a hash rather than a raw password."""
        return _is_hash(self.secret)

    def accepts(self, password: str) -> bool:
        """Whether ``password`` is this agent's."""
        if self.is_hashed:
            return check_password(password, self.secret)
        return _same(password, self.secret)

    @property
    def user(self):
        """The mirror ``User`` row for this agent, created if missing."""
        return _mirror(self.username, self.display_name)


def configured_agents() -> list[Agent]:
    """Parse ``APP_AGENTS`` (or the legacy pair) into agents, in env order.

    Read at call time rather than import time so ``override_settings`` in the
    tests -- and a changed env var after a redeploy -- actually take effect.
    Malformed entries (no colon, blank username or secret) are skipped: a
    typo should cost that one agent their login, not lock out the whole team.
    """
    raw = getattr(settings, "APP_AGENTS", "") or ""
    agents: list[Agent] = []
    seen: set[str] = set()

    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":", 2)]
        if len(parts) < 2:
            continue
        username, secret = parts[0], parts[1]
        display_name = parts[2] if len(parts) > 2 and parts[2] else username
        if not username or not secret or username in seen:
            continue
        seen.add(username)
        agents.append(Agent(username, secret, display_name))

    if agents:
        return agents

    # Legacy single-pair fallback: whatever APP_LOGIN_* holds is the one agent.
    username = getattr(settings, "APP_LOGIN_USERNAME", "") or ""
    secret = getattr(settings, "APP_LOGIN_PASSWORD", "") or ""
    if username and secret:
        return [Agent(username, secret, username)]
    return []


def env_usernames() -> set[str]:
    """The usernames ``APP_AGENTS`` currently claims."""
    return {agent.username for agent in configured_agents()}


def is_env_managed(user) -> bool:
    """Whether this ``User`` row is the mirror of an env-configured agent --
    i.e. whether the app must treat it as read-only."""
    return user.username in env_usernames()


def is_master(user) -> bool:
    """The one permission check the app has."""
    return bool(
        getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False)
    )


def authenticate(username: str, password: str) -> Agent | None:
    """Return the env agent these credentials belong to, or ``None``.

    The username is compared against every configured agent without breaking
    out early, and with ``compare_digest``, so the time this takes doesn't
    leak which usernames exist. Then *one* password verification runs -- the
    matched agent's, or a throwaway hash of the same cost when no username
    matched, which is the trick ``ModelBackend`` uses for the same reason.
    One verification rather than one per agent matters now that verifying is
    deliberately expensive (PBKDF2).

    The throwaway only runs when some agent is hashed. In an all-plaintext
    (deprecated) configuration every comparison is cheap, and burning a
    PBKDF2 on the no-match path would invert the very leak it prevents.
    """
    configured = configured_agents()

    match: Agent | None = None
    for agent in configured:
        if _same(username, agent.username) and match is None:
            match = agent

    if match is None:
        if any(agent.is_hashed for agent in configured):
            make_password(password)
        return None
    return match if match.accepts(password) else None


def _same(a: str, b: str) -> bool:
    """Constant-time equality over the UTF-8 bytes. ``compare_digest`` on
    ``str`` raises TypeError for any non-ASCII character -- a login as "José"
    would 500 -- while bytes of unequal length simply compare False."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _is_hash(secret: str) -> bool:
    """Whether ``secret`` is an encoded password hash rather than a raw one.

    A hash is ``<algorithm>$<rest>`` with an algorithm this project actually
    has a hasher for. Deliberately stricter than ``identify_hasher``, which
    reads any bare 32-character string as an unsalted MD5 digest -- and a
    32-character passphrase is a perfectly ordinary thing to find in an env
    var.
    """
    algorithm, separator, rest = secret.partition("$")
    if not separator or not rest:
        return False
    try:
        get_hasher(algorithm)
    except ValueError:
        return False
    return True


def authenticate_user(request, username: str, password: str):
    """Resolve a login attempt to a ``User``, or ``None``.

    The environment is checked first and wins outright: an env username never
    falls through to the database, even if a row with that name somehow holds
    a usable password (the mirror sync below removes it anyway). Everyone
    else goes through Django's ``ModelBackend``, which is also what rejects
    deactivated accounts and unusable passwords -- so a mirror row can never
    be logged into with the database path.
    """
    agent = authenticate(username, password)
    if agent is not None:
        return agent.user
    if username in env_usernames():
        return None
    user = django_authenticate(request, username=username, password=password)
    # Staff rows are Django-admin accounts (createsuperuser, /admin), not the
    # CRM's: the app never lists, assigns or logs them in. See can_log_in.
    if user is not None and user.is_staff:
        return None
    return user


def env_users() -> list:
    """The mirror ``User`` rows for every env agent, in env order, freshly
    synced to the shape an env account must have (see :func:`_mirror`).

    Steady state is a single SELECT; rows are only written the first time an
    agent appears in the env list or when something (an /admin edit, a
    database account later claimed by the env) left one out of shape.
    """
    agents = configured_agents()
    if not agents:
        return []

    User = get_user_model()
    existing = {
        user.username: user
        for user in User.objects.filter(username__in=[a.username for a in agents])
    }
    users = []
    for agent in agents:
        user = existing.get(agent.username)
        if user is None:
            user = _mirror(agent.username, agent.display_name)
        else:
            _ensure_env_shape(user)
        users.append(user)
    return users


def agent_users() -> list:
    """Everyone who can currently be handed a conversation: the env agents
    first (in env order), then every active database account by name.

    This is what fills the Inbox's assignment dropdown, so it must list
    teammates who have never logged in yet -- an agent you can't assign work to
    until they show up would defeat the point. Left out, because they can't
    answer and offering them is a trap: deactivated accounts, and *ghosts* --
    the mirror rows of agents since removed from ``APP_AGENTS``, which keep
    their unusable password and so have no way to log in until a master gives
    them one in Usuarios (see :func:`can_log_in`).
    """
    env = env_users()
    User = get_user_model()
    others = (
        can_log_in(User.objects.all())
        .exclude(username__in=[user.username for user in env])
        .order_by(Lower("first_name"), "username")
    )
    return env + list(others)


def can_log_in(queryset):
    """Narrow ``queryset`` to the database accounts that can log into *the
    app*: active, with a usable password, and not Django-admin staff.

    The first two are what ModelBackend checks, as a filter -- env mirrors
    fail them on purpose (they log in through the env, not the row) and must
    be added separately. The third is the app's own line: ``is_staff`` rows
    belong to /admin (``createsuperuser``, the seed's demo advisor), and
    treating every User row as a CRM account would hand each of them a CRM
    login, a seat in the assignment dropdown and a row in Usuarios where a
    master could reset their password and walk into /admin with it.
    """
    return queryset.filter(is_active=True, is_staff=False).exclude(
        password__startswith=UNUSABLE_PASSWORD_PREFIX
    )


def assignment_options(conversation) -> list:
    """The dropdown options for one conversation: every assignable user, plus
    whoever it is currently assigned to if they are no longer one.

    That last part is the point. An account can be deactivated, deleted from
    ``APP_AGENTS``, or assigned from a seed script or /admin while its
    conversations stay assigned to it; without an option for it the
    ``<select>`` would fall back to its first entry and quietly claim the chat
    is "Sin asignar". Showing the real assignee -- reassignable, but not
    misrepresented -- is the honest rendering.
    """
    options = agent_users()
    current = conversation.assigned_to
    if current is not None and not any(user.pk == current.pk for user in options):
        options.append(current)
    return options


def _mirror(username: str, display_name: str):
    """Get-or-create the ``User`` row mirroring one env-configured agent."""
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": display_name[:150],
            "is_superuser": True,
            "is_active": True,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    else:
        _ensure_env_shape(user)
    return user


def _ensure_env_shape(user) -> None:
    """Re-assert what an env account is: a master, active, and impossible to
    log into through the database.

    Rows can drift from that -- edited in /admin, deactivated before the
    username was added to ``APP_AGENTS``, or a database account whose name
    the environment later claimed. Claiming is deliberate and total: once the
    env names a username, that username's password is the env one, so any
    usable database password is removed rather than kept as a second door.
    """
    fields = []
    if not user.is_superuser:
        user.is_superuser = True
        fields.append("is_superuser")
    if not user.is_active:
        user.is_active = True
        fields.append("is_active")
    if user.has_usable_password():
        user.set_unusable_password()
        fields.append("password")
    if fields:
        user.save(update_fields=fields)
