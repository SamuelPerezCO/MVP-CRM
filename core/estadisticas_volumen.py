"""
Data layer for the "Volumen de Mensajes" stat screen: the channel palette,
the period filter, and the ORM aggregation behind the KPI tiles and the
daily line chart.

Kept out of ``core.estadisticas`` -- that module is the section's *nav and
card* definition, shared by every stat page; this one is the first card's
report. The remaining three cards get siblings here rather than growing
that file.

All aggregation happens in the database (``TruncDate``/``ExtractHour`` +
``values`` + ``annotate``): the Message table grows without bound and
pulling a period's rows into Python to loop them stops working long before
anyone notices. :func:`report` is cached briefly per date range for the same
reason -- see CACHE_SECONDS.

Days and hours are bucketed in :data:`REPORT_TZ`, not UTC. A message sent at
22:00 Bogotá belongs to that day's point, not the next one's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.db.models import Count
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from messaging.models import Conversation, Message

from .calendario import CALENDAR_TZ

#: The zone days and hours are bucketed in. Same wall clock the calendar
#: enters events in -- one app, one "today".
REPORT_TZ = CALENDAR_TZ

#: The period picker's default span, in days, counting back from today.
DEFAULT_RANGE_DAYS = 30

#: Hard cap on a hand-typed range, so a crafted URL can't ask the database
#: to group a decade.
MAX_RANGE_DAYS = 366

#: How long one range's aggregation is reused. Short enough that a stat
#: screen left open reflects the day's traffic within minutes, long enough
#: that flipping between cards or nudging the picker doesn't re-run the
#: group-by every time.
CACHE_SECONDS = 300


@dataclass(frozen=True)
class Channel:
    """One series in the chart and one column in the accessible table.

    ``light``/``dark`` are the *validated* steps for this channel -- picked
    for colorblind separation and for contrast against their own surface,
    not sampled from the brand's marketing hex (several of those fail
    against white). Dark mode carries its own step on purpose: nothing
    programmatically lightens or flips the light value.
    """

    key: str
    label: str
    light: str
    dark: str


#: The chart's channels, in legend order. This list *is* the color map --
#: a channel's color is looked up by key, never by its index in whatever
#: subset happens to have data, so a channel dropping out of a period can
#: never repaint the survivors.
CHANNELS = [
    Channel("whatsapp", "WhatsApp", "#1DA851", "#16A34A"),
    Channel("messenger", "Messenger", "#0084FF", "#3B82F6"),
    Channel("instagram", "Instagram", "#E4405F", "#EA580C"),
    # Not in the reference, which only charts the three above. TikTok
    # conversations exist in Conversation.CHANNEL_CHOICES, though, and
    # silently dropping their messages would make the tiles disagree with
    # the chart -- so it gets its own step (slate reads as "other" and
    # separates cleanly from the three hues above) and simply never appears
    # while there is no TikTok traffic.
    Channel("tiktok", "TikTok", "#475569", "#94A3B8"),
]

CHANNEL_BY_KEY = {channel.key: channel for channel in CHANNELS}

#: Conversation.CHANNEL_CHOICES key -> chart channel key. Total over that
#: list by construction (the check below), so no message can fall out of the
#: report: a channel added to the model without a home here fails loudly at
#: import instead of quietly vanishing from the totals.
#:
#: The two foldings worth knowing about: Facebook page messages join
#: Messenger (one Meta inbox, and the reference labels it "Messenger"), and
#: Instagram DMs join Instagram.
SOURCE_CHANNEL = {
    "whatsapp": "whatsapp",
    "messenger": "messenger",
    "facebook": "messenger",
    "instagram-dm": "instagram",
    "instagram": "instagram",
    "tiktok-dm": "tiktok",
    "tiktok-coment": "tiktok",
}

assert set(SOURCE_CHANNEL) == {key for key, _ in Conversation.CHANNEL_CHOICES}, (
    "SOURCE_CHANNEL must map every Conversation channel onto a chart channel"
)
assert set(SOURCE_CHANNEL.values()) <= set(CHANNEL_BY_KEY), (
    "SOURCE_CHANNEL points at a channel with no palette entry"
)


# --- Period -----------------------------------------------------------------


def today() -> date:
    """Today in REPORT_TZ -- the period picker's anchor."""
    return timezone.now().astimezone(REPORT_TZ).date()


def default_range() -> tuple[date, date]:
    """The last :data:`DEFAULT_RANGE_DAYS` days, both ends inclusive."""
    end = today()
    return end - timedelta(days=DEFAULT_RANGE_DAYS - 1), end


def parse_range(params) -> tuple[date, date]:
    """Read ``?start=&end=`` (ISO dates) off a QueryDict.

    Anything missing, malformed, inverted or wider than
    :data:`MAX_RANGE_DAYS` falls back to the default window rather than
    erroring: a stale bookmark or a half-typed date should open the screen,
    not 400 it.
    """
    try:
        start = date.fromisoformat(params["start"])
        end = date.fromisoformat(params["end"])
    except (KeyError, TypeError, ValueError):
        return default_range()
    if end < start or (end - start).days + 1 > MAX_RANGE_DAYS:
        return default_range()
    return start, end


def format_range(start: date, end: date) -> str:
    """The picker's displayed value: ``28/07/26 - 28/08/26``."""
    return f"{start:%d/%m/%y} - {end:%d/%m/%y}"


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    """The half-open instant range covering [start, end] in REPORT_TZ.

    Half-open on purpose: an exclusive upper bound of "the day after end,
    at midnight" needs no microsecond fudging and can't drop a message
    stamped at 23:59:59.999.
    """
    lower = datetime.combine(start, datetime.min.time(), tzinfo=REPORT_TZ)
    upper = datetime.combine(
        end + timedelta(days=1), datetime.min.time(), tzinfo=REPORT_TZ
    )
    return lower, upper


# --- Formatting -------------------------------------------------------------


def format_number(value: int) -> str:
    """Spanish thousands grouping: 15268 -> "15.268"."""
    return f"{value:,}".replace(",", ".")


def _hour_suffix(hour24: int) -> str:
    return "a.m." if hour24 < 12 else "p.m."


def _hour12(hour24: int) -> int:
    return hour24 % 12 or 12


def format_hour_band(hour24: int) -> str:
    """An hour-of-day bucket as the tile shows it: ``10 - 11 a.m.``.

    The suffix collapses when both ends share it and is spelled out on both
    when they don't ("11 a.m. - 12 p.m.").
    """
    nxt = (hour24 + 1) % 24
    start_suffix, end_suffix = _hour_suffix(hour24), _hour_suffix(nxt)
    if start_suffix == end_suffix:
        return f"{_hour12(hour24)} - {_hour12(nxt)} {start_suffix}"
    return f"{_hour12(hour24)} {start_suffix} - {_hour12(nxt)} {end_suffix}"


# --- Aggregation ------------------------------------------------------------


def _daily_series(lower, upper, days: list[date]) -> list[dict]:
    """One entry per channel that has traffic, each with a value per day.

    Grouped by (day, conversation channel) in a single query; the per-day
    lists are then filled from that result so gaps read as 0 rather than
    breaking the line.
    """
    rows = (
        Message.objects.filter(timestamp__gte=lower, timestamp__lt=upper)
        .annotate(day=TruncDate("timestamp", tzinfo=REPORT_TZ))
        .values("day", "conversation__channel")
        .annotate(total=Count("id"))
    )

    # channel key -> {day: count}. Source channels fold together here (two
    # Instagram surfaces, one Instagram line).
    counts: dict[str, dict[date, int]] = {channel.key: {} for channel in CHANNELS}
    for row in rows:
        key = SOURCE_CHANNEL.get(row["conversation__channel"])
        if key is None:
            continue  # unreachable while the import-time check holds
        bucket = counts[key]
        bucket[row["day"]] = bucket.get(row["day"], 0) + row["total"]

    series = []
    for channel in CHANNELS:
        bucket = counts[channel.key]
        if not bucket:
            continue  # no traffic this period -- the line simply isn't drawn
        series.append(
            {
                "key": channel.key,
                "label": channel.label,
                "light": channel.light,
                "dark": channel.dark,
                "values": [bucket.get(day, 0) for day in days],
            }
        )
    return series


def _peak_hour(lower, upper) -> dict | None:
    """The busiest hour *of the day* across the period, or None if silent.

    ``ExtractHour`` rather than ``TruncHour``: the tile reads "10 - 11 a.m.
    -- 1942 mensajes", which is every day's 10am hour added together, not one
    specific calendar hour of one specific day.
    """
    row = (
        Message.objects.filter(timestamp__gte=lower, timestamp__lt=upper)
        .annotate(hour=ExtractHour("timestamp", tzinfo=REPORT_TZ))
        .values("hour")
        .annotate(total=Count("id"))
        # Tie-break on the hour so a tie renders the same band every load.
        .order_by("-total", "hour")
        .first()
    )
    if row is None:
        return None
    hour = int(row["hour"])
    return {"hour": hour, "label": format_hour_band(hour), "total": row["total"]}


#: The four KPI tiles, in render order: (key, label, tone, tooltip). ``tone``
#: names the tile's 4px left border in stats-detail.css -- the border is the
#: *only* place the tile's identity is colored. The big number stays dark
#: ink: the border already encodes which tile this is, and repeating that in
#: the number buys nothing while costing legibility (teal on white is a
#: ~2.9:1 numeral, under the 3:1 floor for large text).
TILES = [
    (
        "received",
        "Mensajes recibidos",
        "teal",
        "Mensajes que tus clientes te enviaron en el período, sumando todos "
        "los canales.",
    ),
    (
        "sent",
        "Mensajes enviados",
        "green",
        "Mensajes que tu equipo y tus automatizaciones enviaron en el "
        "período, sumando todos los canales.",
    ),
    (
        "peak",
        "Hora pico",
        "purple",
        "La franja horaria con más mensajes, sumando el mismo horario de "
        "todos los días del período (hora de Colombia).",
    ),
    (
        "total",
        "Mensajes totales",
        "blue",
        "Recibidos más enviados. Es el total de mensajes que pasaron por tu "
        "cuenta en el período.",
    ),
]


def _tiles(totals: dict, peak: dict | None) -> list[dict]:
    """The KPI tiles with their numbers already formatted.

    Formatting lives here rather than in the template and again in the JS:
    the first render and every period change afterwards read the same
    strings out of the same report, so they cannot drift apart.
    """
    values = {
        "received": (
            format_number(totals["received"]),
            f"{totals['received_share']}% del total",
        ),
        "sent": (
            format_number(totals["sent"]),
            f"{totals['sent_share']}% del total",
        ),
        "peak": (
            (peak["label"], f"{format_number(peak['total'])} mensajes")
            if peak
            else ("—", "Sin actividad")
        ),
        "total": (format_number(totals["total"]), "en el período"),
    }
    return [
        {
            "key": key,
            "label": label,
            "tone": tone,
            "tip": tip,
            "value": values[key][0],
            "note": values[key][1],
        }
        for key, label, tone, tip in TILES
    ]


#: Spanish month abbreviations for the table's date column. Mirrors
#: MONTHS_SHORT in static/js/stats_chart.js -- LANGUAGE_CODE is still en-us,
#: so Django's own date filter would print "Jul".
_MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic"]


def _table(days: list[date], series: list[dict]) -> list[dict]:
    """The chart's data as table rows -- one per day, one column per drawn
    channel, plus that day's total.

    This is the chart's accessible equivalent, so it is built from exactly
    the same series (same order, same channels) rather than re-queried: a
    table that could disagree with the chart beside it would be worse than
    no table.
    """
    rows = []
    for index, day in enumerate(days):
        values = [entry["values"][index] for entry in series]
        rows.append(
            {
                "label": f"{day.day} {_MONTHS_SHORT[day.month - 1]}",
                "values": [format_number(value) for value in values],
                "total": format_number(sum(values)),
            }
        )
    return rows


def _build(start: date, end: date) -> dict:
    """The uncached report. See :func:`report` for the caching wrapper."""
    lower, upper = _window(start, end)
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    series = _daily_series(lower, upper, days)

    by_direction = dict(
        Message.objects.filter(timestamp__gte=lower, timestamp__lt=upper)
        .values_list("direction")
        .annotate(total=Count("id"))
    )
    received = by_direction.get(Message.INBOUND, 0)
    sent = by_direction.get(Message.OUTBOUND, 0)
    total = received + sent

    def share(value: int) -> int:
        return round(value * 100 / total) if total else 0

    totals = {
        "received": received,
        "received_share": share(received),
        "sent": sent,
        "sent_share": share(sent),
        "total": total,
    }
    peak = _peak_hour(lower, upper)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "range_label": format_range(start, end),
        "totals": totals,
        "tiles": _tiles(totals, peak),
        "peak_hour": peak,
        # ISO for the JSON consumer; the chart formats its own axis labels.
        "days": [day.isoformat() for day in days],
        "series": series,
        "table": _table(days, series),
    }


def report(start: date, end: date) -> dict:
    """Everything the screen needs for one period, cached for
    :data:`CACHE_SECONDS`.

    Keyed on the range alone. A period ending today therefore trails live
    traffic by up to that long -- an acceptable trade for a dashboard, and
    the reason the window is minutes rather than hours.
    """
    key = f"stats:volumen:{start.isoformat()}:{end.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    built = _build(start, end)
    cache.set(key, built, CACHE_SECONDS)
    return built
