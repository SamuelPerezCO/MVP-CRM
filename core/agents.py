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

    Every configured agent is compared against, without breaking out early, so
    the time this takes doesn't leak which usernames exist; the comparison
    itself is ``compare_digest`` for the same reason.
    """
    match: Agent | None = None
    for agent in configured_agents():
        ok = hmac.compare_digest(username, agent.username) & hmac.compare_digest(
            password, agent.password
        )
        if ok and match is None:
            match = agent
    return match


def agent_users() -> list:
    """The ``User`` rows for every configured agent, in env order.

    This is what fills the Inbox's assignment dropdown, so it must list
    teammates who have never logged in yet -- an agent you can't assign work to
    until they show up would defeat the point. Steady state is a single SELECT;
    rows are only written the first time an agent appears in the env list.
    """
    agents = configured_agents()
    if not agents:
        return []

    User = get_user_model()
    existing = {
        user.username: user
        for user in User.objects.filter(username__in=[a.username for a in agents])
    }
    return [
        existing.get(agent.username) or _mirror(agent.username, agent.display_name)
        for agent in agents
    ]


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
