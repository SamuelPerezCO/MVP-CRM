"""
Data definition for the Inbox screen's left navigation panel (column 2).

Same idea as :mod:`core.nav`: every filter row is declared here once, and the
templates just loop. Adding a channel or renaming a group is a one-line change.

These are *filters* over conversations, not pages -- picking one re-renders the
conversation list (column 3) without touching the rest of the screen.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Filter:
    """One selectable row in the Inbox nav panel."""

    key: str
    """Slug used in the URL, e.g. ``todos`` -> /s/inbox/?filter=todos."""

    label: str
    """Visible Spanish label."""

    icon: str
    """Template path under ``templates/`` (line icon or brand mark)."""

    show_count: bool = False
    """Right-align a count badge on the row (channels do, the others don't)."""

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


@dataclass(frozen=True)
class FilterGroup:
    """A titled block of filters in the nav panel."""

    title: str
    items: list[Filter]


# Group 1 -- ownership of the conversation.
CONVERSATIONS = [
    Filter("todos",        "Todos",        "users"),
    Filter("tu-inbox",     "Tu inbox",     "inbox"),
    Filter("sin-asignar",  "Sin asignar",  "user"),
]

# Group 2 -- MIA is the AI assistant; these filter by what it detected.
MIA = [
    Filter("mia-activa",       "MIA activa",        "zap"),
    Filter("intencion-compra", "Intención compra",  "shopping-bag"),
    Filter("ayuda-humana",     "Ayuda humana",      "hand"),
]

# Group 3 -- source channel. Brand marks keep their own colours.
CANALES = [
    Filter("whatsapp",       "WhatsApp",            "brands/whatsapp",     show_count=True),
    Filter("messenger",      "Messenger",           "brands/messenger",    show_count=True),
    Filter("instagram-dm",   "Instagram DM",        "brands/instagram-dm", show_count=True),
    Filter("facebook",       "Facebook",            "brands/facebook",     show_count=True),
    Filter("instagram",      "Instagram",           "brands/instagram",    show_count=True),
    Filter("tiktok-dm",      "Tiktok DM",           "brands/tiktok",       show_count=True),
    Filter("tiktok-coment",  "Tiktok comentarios",  "brands/tiktok",       show_count=True),
]

FILTER_GROUPS = [
    FilterGroup("Conversaciones", CONVERSATIONS),
    FilterGroup("MIA", MIA),
    FilterGroup("Canales", CANALES),
]

ALL_FILTERS = [item for group in FILTER_GROUPS for item in group.items]
FILTER_BY_KEY = {item.key: item for item in ALL_FILTERS}

#: Selected when the Inbox first opens.
DEFAULT_FILTER = "todos"


def get_conversations(filter_key: str):
    """Return the conversations matching ``filter_key``.

    Placeholder for the real queryset. Returning an empty list is what makes
    column 3 render its empty state; once a Conversation model exists this
    becomes the filtered lookup and the list template starts drawing rows
    without any template change.
    """
    return []


def get_counts() -> dict[str, int]:
    """Per-channel unread counts keyed by filter key. All zero until there's data."""
    return {item.key: 0 for item in ALL_FILTERS}
