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

``APP_AGENTS`` format -- comma-separated ``username:password:Nombre`` entries::

    APP_AGENTS=Admin:sup3rsecret:Admin,Samuel:1234:Samuel

The display name is optional (``username:password`` falls back to the
username). Colons and commas can't appear in a password, since they are the
separators.

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


@dataclass(frozen=True)
class Agent:
    """One configured agent, straight from the environment."""

    username: str
    password: str
    display_name: str

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
        username, password = parts[0], parts[1]
        display_name = parts[2] if len(parts) > 2 and parts[2] else username
        if not username or not password or username in seen:
            continue
        seen.add(username)
        agents.append(Agent(username, password, display_name))

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

    The env list first: every configured agent is compared against, without
    breaking out early, so the time this takes doesn't leak which usernames
    exist; the comparison itself is ``compare_digest`` for the same reason.
    Then the database: a user created from the Usuarios page has a usable
    password and is checked by Django's own hasher. Env mirrors never reach
    that step -- their password is unusable (see :func:`_mirror`), so a
    username that is in the env list can only log in with the env password.
    """
    match: Agent | None = None
    for agent in configured_agents():
        ok = hmac.compare_digest(username, agent.username) & hmac.compare_digest(
            password, agent.password
        )
        if ok and match is None:
            match = agent
    if match is not None:
        return match

    if not username or not password:
        return None
    # An env username never falls through to the database, whatever its
    # mirror row's password field holds (a seed or /admin could set one):
    # the env is the only way in for those accounts.
    if username in {agent.username for agent in configured_agents()}:
        return None
    User = get_user_model()
    user = User.objects.filter(username=username, is_active=True).first()
    if user is None or not _is_app_user(user) or not user.check_password(password):
        return None
    return Agent(user.username, "", user.get_full_name() or user.username)


def _is_app_user(user) -> bool:
    """A row someone can actually log in with: a real, usable password.
    Env mirrors have an unusable one and rows a seed or /admin made without
    a password have an empty one -- neither is a teammate."""
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
    assigned from a seed script, or from /admin) while their conversations
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
    user.first_name = (display_name or user.username)[:150]
    fields = ["first_name"]
    if password:
        user.set_password(password)
        fields.append("password")
    user.save(update_fields=fields)
    _set_master(user, master)
    return user


def set_user_active(user, active: bool):
    """Deactivate (or restore) an app-created user. Deactivating is the only
    "delete": their conversations, messages and events keep pointing at
    them, they just can't log in or be assigned anything new."""
    if is_env_agent(user):
        raise ValueError("Este usuario se configura en el entorno (APP_AGENTS), no aquí.")
    user.is_active = active
    user.save(update_fields=["is_active"])
    return user
