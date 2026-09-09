"""The people who answer conversations -- "agentes" in the UI.

An agent is a login *and* an assignee: the same identity that gets past the
gate in :mod:`core.middleware` is the one a conversation can be assigned to in
the Inbox. Every one of them is a ``django.contrib.auth`` ``User`` row with a
real, usable password -- **the database is the only source of truth** for who
can log in, what their password is, whether they are a master and whether
they are still active. CRM > Equipo > Usuarios is where a master manages all
of that, with no redeploy.

**Seeding from the environment.** A fresh database holds nobody, and the
Usuarios page needs a master to open it. ``APP_AGENTS`` is how the first
masters get in -- comma-separated ``username:hash:Nombre`` entries::

    APP_AGENTS=Admin:pbkdf2_sha256$1500000$SALT$HASH=:Admin

The middle field is a password *hash* (``manage.py hashear_clave`` prints
one). :func:`import_env_agents` turns each entry into a real ``User`` row,
hash and all, in the Maestros group. It runs before every login and before
every listing, and it is a **one-time import per username**: once the row has
a usable password the environment is never consulted for it again. That is
what lets a password changed on the Usuarios page stick, a teammate be
deactivated even though the env still names them, and the env variable be
removed altogether once the team is in the database. A row that predates
this module -- a mirror with an unusable password -- is converted in place,
so its id, and everything attributed to it, survives.

The display name is optional (``username:hash`` falls back to the username).
Colons and commas can't appear in the middle field, since they are the
separators -- Django's default PBKDF2 hashes contain neither. A raw password
is still accepted there so no redeploy locks a team out, but :mod:`core.checks`
warns (``core.W001``) for every agent still configured that way.

If ``APP_AGENTS`` is unset the older single pair
(``APP_LOGIN_USERNAME``/``APP_LOGIN_PASSWORD``) is used as a one-agent seed,
so an environment that predates this module keeps working untouched. With
neither set, ``manage.py crear_maestro`` creates the first master directly.

**Masters** are the users in the "Maestros" group (:func:`is_master`), plus
any Django superuser; only they manage users. Seeded agents land in the
group on import. The last master who can actually log in can never be
demoted or deactivated (:class:`LastMaster`), or the team could lock itself
out with nobody able to fix it.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import (
    UNUSABLE_PASSWORD_PREFIX,
    check_password,
    get_hasher,
    make_password,
)
from django.db.models import Q
from django.db.models.functions import Lower


@dataclass(frozen=True)
class Agent:
    """One seed entry, straight from the environment."""

    username: str

    secret: str
    """This agent's password *hash* -- or, deprecated, a raw password.

    Named for what it holds rather than what it is: :attr:`encoded` is what
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
    def encoded(self) -> str:
        """The hash to store in the ``User`` row: the env's own when it is
        one, otherwise the raw password hashed now."""
        return self.secret if self.is_hashed else make_password(self.secret)


def configured_agents() -> list[Agent]:
    """Parse ``APP_AGENTS`` (or the legacy pair) into seed agents, in env order.

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


def import_env_agents() -> list:
    """Make sure every seed agent has a real ``User`` row; return those rows
    in env order.

    A username with no row gets one: the env's hash as its password, the
    env's display name, the master role. A row with no usable password (a
    mirror from before the database owned logins, or a seed's assignee-only
    row) is converted in place -- same id, so every conversation and message
    attributed to it stays attributed. A row that already has a usable
    password is left exactly as it is: the database owns it now, whatever
    the env still says. Steady state is one SELECT and no writes.
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
            user = User(username=agent.username, first_name=agent.display_name[:150])
            user.password = agent.encoded
            user.save()
            _set_master(user, True)
        elif not user.has_usable_password():
            user.password = agent.encoded
            if not user.first_name:
                user.first_name = agent.display_name[:150]
            user.save(update_fields=["password", "first_name"])
            _set_master(user, True)
        users.append(user)
    return users


def authenticate(username: str, password: str):
    """Return the ``User`` these credentials belong to, or ``None``.

    Seeds are imported first, so an agent named only in the environment can
    log in on a fresh database; after that it is the database alone: an
    active row with a usable password, checked by Django's own hasher. A
    deactivated user is turned away whatever the env says, and so is a Django
    admin account -- /admin is a different door (see :func:`_is_app_user`).

    Exactly one password verification runs per call: the matched user's, or
    a throwaway of equal cost when nothing matched (the trick ``ModelBackend``
    uses), so a hit and a miss take the same time and leak nothing about
    which usernames exist.
    """
    import_env_agents()
    if not username or not password:
        return None
    User = get_user_model()
    user = User.objects.filter(username=username, is_active=True).first()
    if user is None or not _is_app_user(user):
        make_password(password)   # equal-cost miss; result discarded
        return None
    if not user.check_password(password):
        return None
    return user


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

    Public because the Usuarios dialog, ``manage.py crear_maestro`` and
    ``manage.py hashear_clave`` apply the same rule: a password reaching the
    database by any road should clear the same bar.
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

    Rows made without a password (an old seed's assignee, a script's) are not
    teammates. ``is_staff`` is the load-bearing part: it means "may open
    /admin/", a door this CRM does not manage. Listing such a row here would
    let a CRM master reset its password and walk into the Django admin, which
    is a bigger key than the page grants.
    """
    if user.is_staff or user.is_superuser:
        return False
    return bool(user.password) and user.has_usable_password()


def agent_users() -> list:
    """The ``User`` rows for every agent: everyone active with a real
    password, by display name.

    This is what fills the Inbox's assignment dropdown, so it must list
    teammates who have never logged in yet -- an agent you can't assign work to
    until they show up would defeat the point. Seeds are imported first so a
    fresh deployment's masters are on the list before their first login.
    """
    import_env_agents()
    User = get_user_model()
    return [
        user
        for user in User.objects.filter(is_active=True).order_by(
            Lower("first_name"), "username"
        )
        if _is_app_user(user)
    ]


def assignment_options(conversation) -> list:
    """The dropdown options for one conversation: every agent, plus whoever
    it is currently assigned to if they are no longer one.

    That last part is the point. An agent can be deactivated (or be assigned
    from /admin, or by the automation writing into the database) while their
    conversations stay assigned to them; without an option for them the
    ``<select>`` would fall back to its first entry and quietly claim the chat
    is "Sin asignar". Showing the real assignee -- reassignable, but not
    misrepresented -- is the honest rendering.
    """
    options = agent_users()
    current = conversation.assigned_to
    if current is not None and not any(user.pk == current.pk for user in options):
        options.append(current)
    return options


# --- Team management (CRM > Equipo > Usuarios) ------------------------------


#: Django group carrying the master role. A group rather than ``is_staff``
#: on purpose: ``is_staff`` means "may open /admin/", a different question
#: from "may manage this CRM's team" -- the seed marks its demo advisor
#: staff for /admin access, and that must not make them a master here.
#: Built-in model, so no migration of our own.
MASTER_GROUP = "Maestros"


def is_master(user) -> bool:
    """Whether ``user`` may manage the team: a Django superuser, or a user
    in the Maestros group -- which is where seeded agents land on import."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=MASTER_GROUP).exists()


def is_app_user(user) -> bool:
    """Public face of :func:`_is_app_user` for the Usuarios page."""
    return _is_app_user(user)


class UsernameTaken(Exception):
    """Another user already has this username."""


def create_user(username: str, password: str, display_name: str = "", master: bool = False):
    """Create a teammate who can log in with ``password``.

    Raises :class:`UsernameTaken` -- including for a seed username that has
    not been imported yet, since the import happens first.
    """
    import_env_agents()
    User = get_user_model()
    username = username.strip()
    if User.objects.filter(username__iexact=username).exists():
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
    """Rename, promote/demote and optionally reset the password of a user.
    Applies to seeded agents too: once imported, the database owns them."""
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
    """How many masters able to log in would remain.

    Seeds are imported first so a master named only in the environment
    counts -- they can log in. A master on paper with no usable password
    (a row left behind by a script, or deactivated) cannot, and counting
    them as the survivor would let the last real master go and lock the
    team out.
    """
    import_env_agents()
    User = get_user_model()
    masters = (
        User.objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(groups__name=MASTER_GROUP))
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
    """Deactivate (or restore) a user. Deactivating is the only "delete":
    their conversations, messages and events keep pointing at them, they
    just can't log in or be assigned anything new -- and that holds for a
    seeded agent too, whatever the env still says."""
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
