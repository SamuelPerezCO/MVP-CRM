"""
Data definition for the Inbox screen's left navigation panel (column 2).

Same idea as :mod:`core.nav`: every filter row is declared here once, and the
templates just loop. Adding a channel or renaming a group is a one-line change.

These are *filters* over conversations, not pages -- picking one re-renders the
conversation list (column 3) without touching the rest of the screen.
"""

from dataclasses import dataclass

from django.db.models import Count, F

from messaging.models import Conversation


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


#: Filter keys that map straight onto Conversation.channel values.
CHANNEL_KEYS = {item.key for item in CANALES}


def get_conversations(filter_key: str, user=None, tag_ids=None):
    """Return the conversations matching ``filter_key``, newest activity first.

    ``user`` matters only for "tu-inbox" (conversations assigned to the
    request's user); anonymous visitors get an empty list there, since nothing
    can be assigned to them.

    ``tag_ids`` narrows further with AND semantics -- a conversation must
    carry *all* the selected tags -- and composes with the nav filter rather
    than replacing it. Tags render on every row, so they are prefetched in
    one extra query instead of one per row.
    """
    queryset = (
        Conversation.objects.select_related("contact", "assigned_to")
        .prefetch_related("tags")
        # nulls_last: a just-created conversation with no messages yet sinks
        # to the bottom instead of masquerading as the most recent.
        .order_by(F("last_message_at").desc(nulls_last=True), "-id")
    )

    # AND across tags: each chained .filter() on an M2M adds its own join,
    # so every selected tag must be present (one .filter(tags__in=...) would
    # be OR).
    for tag_id in tag_ids or []:
        queryset = queryset.filter(tags=tag_id)

    if filter_key == "todos":
        return queryset
    if filter_key == "tu-inbox":
        if user is None or not getattr(user, "is_authenticated", False):
            return queryset.none()
        return queryset.filter(assigned_to=user)
    if filter_key == "sin-asignar":
        return queryset.filter(assigned_to__isnull=True)
    if filter_key in CHANNEL_KEYS:
        return queryset.filter(channel=filter_key)
    # MIA filters have nothing behind them yet (no AI-detection model), so
    # they honestly show the empty state rather than pretending.
    return queryset.none()


def get_counts() -> dict[str, int]:
    """Per-filter count badges, keyed by filter key.

    Channels (the only rows with ``show_count``) show how many conversations
    hold unread messages -- one aggregate query, zero-filled so every key is
    always present for the template's ``counts|dict_get:row.key``.
    """
    counts = {item.key: 0 for item in ALL_FILTERS}
    unread_per_channel = (
        Conversation.objects.filter(unread_count__gt=0)
        .values("channel")
        .annotate(n=Count("id"))
    )
    for row in unread_per_channel:
        counts[row["channel"]] = row["n"]
    return counts
