"""
Data definition and helpers for the CRM's Mi calendario screen: the event
type palette, the sidebar's preference choices (kept in the session until
real users/auth exist) and the timezone plumbing every calendar endpoint
goes through.

Times are stored in UTC (USE_TZ) and entered/rendered in CALENDAR_TZ.
FullCalendar runs with a named timeZone and no timezone plugin, so its Date
objects are UTC-coerced wall clocks; parse_client_dt is the single place
that interpretation happens -- nothing else in the calendar code may parse
a datetime string.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from .models import CalendarEvent

#: Wall-clock zone for entry and display: fixed UTC-5, no DST.
CALENDAR_TZ = ZoneInfo("America/Bogota")


@dataclass(frozen=True)
class EventType:
    """One entry in the modal's Tipo selector. ``color`` names the tag
    palette pair (tags.css --tag-<color>-bg/-fg) the grid reuses, so event
    colors and tag pills stay one system."""

    key: str
    label: str
    color: str

    @property
    def css_class(self) -> str:
        return f"cal-event--{self.color}"


#: Type key -> tag palette color. Keys and labels come from the model's
#: TYPE_CHOICES (single source, matching core.plantillas' label stance);
#: a type added there without a color here fails loudly at import.
_TYPE_COLORS = {
    "llamada": "green",
    "reunion": "blue",
    "seguimiento": "yellow",
    "otro": "gray",
}

#: Order follows TYPE_CHOICES, which is the order rendered in the modal.
EVENT_TYPES = [
    EventType(key, label, _TYPE_COLORS[key])
    for key, label in CalendarEvent.TYPE_CHOICES
]

EVENT_TYPE_BY_KEY = {event_type.key: event_type for event_type in EVENT_TYPES}
DEFAULT_EVENT_TYPE = "reunion"

#: FullCalendar slotDuration values, offered as-is in the settings card.
SLOT_CHOICES = [
    ("00:15:00", "15 minutos"),
    ("00:30:00", "30 minutos"),
    ("01:00:00", "60 minutos"),
]
SLOT_KEYS = {key for key, _ in SLOT_CHOICES}
DEFAULT_SLOT = "00:30:00"

REMINDER_CHOICES = [
    ("", "Sin recordatorio"),
    ("5", "5 minutos antes"),
    ("15", "15 minutos antes"),
    ("30", "30 minutos antes"),
    ("60", "1 hora antes"),
    ("1440", "1 día antes"),
]

#: The menu is the contract: off-menu reminder values are rejected rather
#: than stored (they would round-trip invisibly through the modal anyway).
REMINDER_KEYS = {key for key, _ in REMINDER_CHOICES if key}

_WEEKENDS_KEY = "calendar_weekends"
_SLOT_KEY = "calendar_slot"


def get_prefs(session) -> dict:
    """The sidebar preferences, defaulted and validated. Session-kept until
    the app grows real users/auth -- then this becomes a UserPreference
    lookup with the same shape."""
    slot = session.get(_SLOT_KEY, DEFAULT_SLOT)
    if slot not in SLOT_KEYS:
        slot = DEFAULT_SLOT
    return {
        "weekends": bool(session.get(_WEEKENDS_KEY, True)),
        "slot": slot,
    }


def set_prefs(session, weekends: bool, slot: str) -> None:
    """Persist the sidebar preferences; an unknown slot falls back."""
    session[_WEEKENDS_KEY] = weekends
    session[_SLOT_KEY] = slot if slot in SLOT_KEYS else DEFAULT_SLOT


def parse_client_dt(value: str) -> datetime:
    """A datetime string from the calendar UI -> aware UTC datetime.

    Naive strings are CALENDAR_TZ wall clocks; a trailing Z is stripped
    first because FullCalendar's UTC-coercion emits wall clocks marked Z.
    Strings carrying a real offset are honored as-is. Raises ValueError on
    anything unparseable.
    """
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CALENDAR_TZ)
    try:
        return parsed.astimezone(dt_timezone.utc)
    except OverflowError as exc:
        # Year-9999 wall clocks cross datetime.max when shifted to UTC;
        # surface them as the invalid input they are.
        raise ValueError(str(exc)) from exc


def normalize_all_day(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Snap an all-day range to CALENDAR_TZ midnights: start floored to its
    day, end ceiled to an exclusive midnight at least one day later. Drags
    into the all-day lane arrive with arbitrary wall clocks (FullCalendar
    even drops the end entirely); this is the single place they get sane."""
    local_start = start.astimezone(CALENDAR_TZ)
    floored = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = end.astimezone(CALENDAR_TZ)
    ceiled = local_end.replace(hour=0, minute=0, second=0, microsecond=0)
    if local_end > ceiled:
        ceiled += timedelta(days=1)
    ceiled = max(ceiled, floored + timedelta(days=1))
    return floored.astimezone(dt_timezone.utc), ceiled.astimezone(dt_timezone.utc)


def serialize_event(event) -> dict:
    """One CalendarEvent as the dict FullCalendar consumes: ISO strings with
    the CALENDAR_TZ offset; all-day events as plain dates (end exclusive)."""
    start = event.start.astimezone(CALENDAR_TZ)
    end = event.end.astimezone(CALENDAR_TZ)
    event_type = EVENT_TYPE_BY_KEY.get(
        event.event_type, EVENT_TYPE_BY_KEY[DEFAULT_EVENT_TYPE]
    )
    return {
        "id": event.pk,
        "title": event.title,
        "start": start.date().isoformat() if event.all_day else start.isoformat(),
        "end": end.date().isoformat() if event.all_day else end.isoformat(),
        "allDay": event.all_day,
        "classNames": [event_type.css_class],
        "extendedProps": {
            "description": event.description,
            "eventType": event.event_type,
            "contactId": event.contact_id or "",
            "contactName": event.contact.full_name if event.contact else "",
            "assignedToId": event.assigned_to_id or "",
            "reminder": (
                "" if event.reminder_minutes_before is None
                else event.reminder_minutes_before
            ),
        },
    }
