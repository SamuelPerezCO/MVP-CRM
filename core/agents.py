"""The people who answer conversations -- "agentes" in the UI.

An agent is a login *and* an assignee: the same identity that gets past the
gate in :mod:`core.middleware` is the one a conversation can be assigned to in
the Inbox. Two things have to line up for that:

* Credentials live in the environment (``APP_AGENTS``), not the database, so
  adding a teammate is an env-var edit and a redeploy -- there is no signup,
  no password reset and no user-management screen to build.
* ``Conversation.assigned_to`` is a FK to ``AUTH_USER_MODEL``, so every
  configured agent needs a real Django ``User`` row to point at. Those rows
  are *mirrors*, created on demand from the env list (see :func:`agent_users`)
  with an unusable password -- the environment stays the only source of truth
  for who can log in, and nobody can authenticate through the ORM.

``APP_AGENTS`` format -- comma-separated ``username:hash:Nombre`` entries::

    APP_AGENTS=Admin:pbkdf2_sha256$1500000$SALT$HASH=:Admin

The middle field is a password *hash*, not a password: ``manage.py
hashear_clave`` generates one, and :meth:`Agent.accepts` verifies it with
``check_password`` at the same PBKDF2 cost as a database account. The display
name is optional (``username:hash`` falls back to the username). Colons and
commas can't appear in the middle field, since they are the separators --
Django's default PBKDF2 hashes contain neither.

A raw password is still accepted there so no redeploy locks a team out, but
:mod:`core.checks` warns (``core.W001``) for every agent still configured
that way.

If ``APP_AGENTS`` is unset the older single pair
(``APP_LOGIN_USERNAME``/``APP_LOGIN_PASSWORD``) is used as a one-agent list, so
an environment that predates this module keeps working untouched.

**Users created in the app.** The env list is where the *master* users come
from -- whoever configured the deployment. From CRM > Equipo > Usuarios a
master creates the rest of the team as ordinary ``User`` rows with a real
(usable) password; :func:`authenticate` checks the env list first and the
database second, and :func:`agent_users` lists both, so a teammate created
in the app can log in, be assigned conversations and appear in every
dropdown with no redeploy. Masters are the env agents plus any DB user
in the "Maestros" group (:func:`is_master`); only they manage users.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.contrib.auth.hashers import (
    UNUSABLE_PASSWORD_PREFIX,
    check_password,
    get_hasher,
    make_password,
)


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
    Malformed entries (no colon, blank username or password) are skipped: a
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
    password = getattr(settings, "APP_LOGIN_PASSWORD", "") or ""
    if username and password:
        return [Agent(username, password, username)]
    return []


def authenticate(username: str, password: str) -> Agent | None:
    """Return the agent these credentials belong to, or ``None``.

    The environment is checked first and wins outright. Every configured
    username is compared without breaking out early, so the time this takes
    doesn't leak which ones exist, and exactly *one* password verification
    runs: the matched agent's, or a throwaway of equal cost when nothing
    matched (the trick ``ModelBackend`` uses), so a hit and a miss cost the
    same. The throwaway is skipped when no agent is hashed -- verifying a
    raw password is free, and paying for a hash there would invert the leak.

    Then the database: a user created from the Usuarios page has a usable
    password, checked by Django's own hasher. An env username never reaches
    that step, whatever its mirror row holds -- the environment stays the
    only way into those accounts.
    """
    agents = configured_agents()
    match: Agent | None = None
    for agent in agents:
        if _same(username, agent.username) and match is None:
            match = agent

    if match is not None:
        return match if match.accepts(password) else None
    if any(agent.is_hashed for agent in agents):
        make_password(password)   # equal-cost miss; result discarded

    if not username or not password:
        return None
    User = get_user_model()
    user = User.objects.filter(username=username, is_active=True).first()
    if user is None or not _is_app_user(user) or not user.check_password(password):
        return None
    return Agent(user.username, "", user.get_full_name() or user.username)


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


#: Floor for a password this app sets, wherever it is set from.
MIN_PASSWORD_LENGTH = 8


class WeakPassword(Exception):
    """The password does not clear :func:`validate_password`'s floor."""


def validate_password(password: str, username: str = "") -> None:
    """A small, Spanish-worded floor -- the project's AUTH_PASSWORD_VALIDATORS
    would say the same things in English, in an all-Spanish UI.

    Public because the Usuarios dialog and ``manage.py hashear_clave`` apply
    the same rule: a password reaching APP_AGENTS as a hash should clear the
    same bar as one typed into the dialog.
    """
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    if password.isdigit():
        raise WeakPassword("La contraseña no puede ser solo números.")
    if username and password.casefold() == username.casefold():
        raise WeakPassword("La contraseña no puede ser igual al usuario.")


def _is_app_user(user) -> bool:
    """A row this app's Usuarios page owns: a real, usable password, and not
    a Django staff account.

    Env mirrors have an unusable password and rows made without one have an
    empty one -- neither is a teammate. ``is_staff`` is the load-bearing
    part: it means "may open /admin/", a door this CRM does not manage.
    Listing such a row here would let a CRM master reset its password and
    walk into the Django admin, which is a bigger key than the page grants.
    """
    if user.is_staff or user.is_superuser:
        return False
    return bool(user.password) and user.has_usable_password()


def agent_users() -> list:
    """The ``User`` rows for every agent: the env list in env order, then
    the users created in the app (active, with a real password), by name.

    This is what fills the Inbox's assignment dropdown, so it must list
    teammates who have never logged in yet -- an agent you can't assign work to
    until they show up would defeat the point. Steady state is two SELECTs;
    env rows are only written the first time an agent appears in the list.
    """
    agents = configured_agents()
    User = get_user_model()
    env_names = [a.username for a in agents]
    existing = {
        user.username: user for user in User.objects.filter(username__in=env_names)
    }
    users = [
        existing.get(agent.username) or _mirror(agent.username, agent.display_name)
        for agent in agents
    ]
    # App-created teammates -- see _is_app_user for what separates them from
    # env mirrors and from password-less rows.
    for user in (
        User.objects.filter(is_active=True)
        .exclude(username__in=env_names)
        .order_by("first_name", "username")
    ):
        if _is_app_user(user):
            users.append(user)
    return users


def assignment_options(conversation) -> list:
    """The dropdown options for one conversation: every configured agent, plus
    whoever it is currently assigned to if they are no longer one.

    That last part is the point. An agent can leave ``APP_AGENTS`` (or be
    assigned from /admin, or by the automation writing into the database)
    while their conversations
    stay assigned to them; without an option for them the ``<select>`` would
    fall back to its first entry and quietly claim the chat is "Sin asignar".
    Showing the real assignee -- reassignable, but not misrepresented -- is the
    honest rendering.
    """
    options = agent_users()
    current = conversation.assigned_to
    if current is not None and not any(user.pk == current.pk for user in options):
        options.append(current)
    return options


def _mirror(username: str, display_name: str):
    """Get-or-create the ``User`` row mirroring one env-configured agent.

    ``set_unusable_password`` is the point: these rows exist to be pointed at
    by ``assigned_to`` and ``sent_by``, never to authenticate. The env list is
    the only way in.
    """
    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=username, defaults={"first_name": display_name[:150]}
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


# --- Team management (CRM > Equipo > Usuarios) ------------------------------


#: Django group carrying the master role. A group rather than ``is_staff``
#: on purpose: ``is_staff`` means "may open /admin/", a different question
#: from "may manage this CRM's team" -- the seed marks its demo advisor
#: staff for /admin access, and that must not make them a master here.
#: Built-in model, so no migration of our own.
MASTER_GROUP = "Maestros"


def is_master(user) -> bool:
    """Whether ``user`` may manage the team: an env-configured agent (they
    own the deployment), a Django superuser, or a DB user a master put in
    the Maestros group."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if user.username in {agent.username for agent in configured_agents()}:
        return True
    return user.groups.filter(name=MASTER_GROUP).exists()


def is_app_user(user) -> bool:
    """Public face of :func:`_is_app_user` for the Usuarios page."""
    return _is_app_user(user)


def is_env_agent(user) -> bool:
    """Whether this row mirrors an env-configured agent -- whose password and
    existence live in the environment, so the page can only *show* them."""
    return user.username in {agent.username for agent in configured_agents()}


class UsernameTaken(Exception):
    """Another user already has this username."""


def create_user(username: str, password: str, display_name: str = "", master: bool = False):
    """Create a teammate who can log in with ``password``.

    Raises :class:`UsernameTaken` -- including for env usernames, whose
    mirror rows exist (or will) and must keep their unusable password.
    """
    User = get_user_model()
    username = username.strip()
    if User.objects.filter(username__iexact=username).exists() or username in {
        agent.username for agent in configured_agents()
    }:
        raise UsernameTaken(f"Ya existe un usuario llamado «{username}».")
    user = User(username=username, first_name=(display_name or username)[:150])
    user.set_password(password)
    user.save()
    _set_master(user, master)
    return user


def _set_master(user, master: bool) -> None:
    """Put the user in (or out of) the Maestros group, creating it on first
    use so a fresh deployment needs no fixture."""
    from django.contrib.auth.models import Group

    group, _ = Group.objects.get_or_create(name=MASTER_GROUP)
    if master:
        user.groups.add(group)
    else:
        user.groups.remove(group)


def update_user(user, display_name: str, master: bool, password: str = ""):
    """Rename, promote/demote and optionally reset the password of an
    app-created user. Env mirrors are refused: their identity is the env's."""
    if is_env_agent(user):
        raise ValueError("Este usuario se configura en el entorno (APP_AGENTS), no aquí.")
    if not master:
        _guard_last_master(user)
    user.first_name = (display_name or user.username)[:150]
    fields = ["first_name"]
    if password:
        user.set_password(password)
        fields.append("password")
    user.save(update_fields=fields)
    _set_master(user, master)
    return user


def _master_count(exclude_pk=None) -> int:
    """How many masters would remain. Env agents count: while APP_AGENTS
    names anybody, the team can always be administered."""
    if configured_agents():
        return 2   # any positive number above the guard's floor
    User = get_user_model()
    masters = (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(groups__name=MASTER_GROUP))
        # Only masters who could actually log in. A mirror row left behind by
        # an agent dropped from APP_AGENTS is still active and still a master
        # on paper, but has an unusable password -- counting it as the
        # survivor would let the last real master go and lock the team out.
        .exclude(password="")
        .exclude(password__startswith=UNUSABLE_PASSWORD_PREFIX)
    )
    if exclude_pk is not None:
        masters = masters.exclude(pk=exclude_pk)
    return masters.distinct().count()


class LastMaster(Exception):
    """Refused: the change would leave nobody able to manage the team."""


def _guard_last_master(user) -> None:
    """Refuse a demotion/deactivation that removes the final master."""
    if not is_master(user):
        return
    if _master_count(exclude_pk=user.pk) == 0:
        raise LastMaster(
            "Es el único usuario maestro: nombra a otro antes de quitarle el rol "
            "o desactivarlo."
        )


def set_user_active(user, active: bool):
    """Deactivate (or restore) an app-created user. Deactivating is the only
    "delete": their conversations, messages and events keep pointing at
    them, they just can't log in or be assigned anything new."""
    if is_env_agent(user):
        raise ValueError("Este usuario se configura en el entorno (APP_AGENTS), no aquí.")
    if not active:
        _guard_last_master(user)
    user.is_active = active
    user.save(update_fields=["is_active"])
    if not active:
        end_sessions(user)
    return user


def end_sessions(user) -> int:
    """Drop every live session belonging to ``user``; returns how many.

    Deactivating a row only stops the *next* login unless the sessions it
    already has are cleared -- otherwise someone just locked out keeps
    browsing until their cookie expires.
    """
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    ended = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if str(session.get_decoded().get("_auth_user_id", "")) == str(user.pk):
            session.delete()
            ended += 1
    return ended
