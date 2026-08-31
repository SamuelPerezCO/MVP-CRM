"""
Data layer for the "Tiempos de Respuesta" stat screen: the agent/platform
filters and the pairing walk behind the KPI tiles and the distribution chart.

Sibling of ``core.estadisticas_volumen`` (see that module's header for why
these live outside ``core.estadisticas``), and it reuses volumen's period
helpers so both screens agree on what "the period" means.

Unlike volumen, the core numbers here are *sequence* facts -- "how long
after the customer's message did the next reply land" -- which a group-by
cannot express. So this module streams the period's messages in conversation
order (four columns, ``values_list(...).iterator()``) and pairs them in one
O(1)-memory pass, instead of aggregating in the database. The stream is
bounded by the same MAX_RANGE_DAYS cap volumen enforces, and the result is
cached per (range, filters) just like volumen's report.

Definitions, which the "¿Cómo funciona?" dialog repeats in prose:

* A **response** is the first outbound message after a run of inbound
  messages, measured from the *first* message of that run -- the moment the
  customer started waiting, not the moment they stopped typing.
* An **escalation** ("post-MIA") is a human outbound message (``sent_by``
  set) that directly follows an automated outbound one (``sent_by`` NULL)
  with no customer message in between: the bot answered, then a person
  stepped in. Its time is the gap between those two outbound messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache

from messaging.models import Message

from .estadisticas_volumen import (  # noqa: F401  (re-exported for the view)
    CACHE_SECONDS,
    SOURCE_CHANNEL,
    _window,
    default_range,
    format_number,
    format_range,
    parse_range,
)

#: The platform filter's options: the same folded channels the volumen chart
#: draws, so "Instagram" means the same set of conversations on both screens.
#: Built by inverting SOURCE_CHANNEL -- a Conversation channel added later
#: joins the filter through that one map, not through a second list here.
PLATFORMS = [
    ("whatsapp", "WhatsApp"),
    ("messenger", "Messenger"),
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
]

PLATFORM_BY_KEY = dict(PLATFORMS)

#: Platform key -> the Conversation.channel values it covers.
PLATFORM_CHANNELS: dict[str, list[str]] = {key: [] for key, _ in PLATFORMS}
for _source, _target in SOURCE_CHANNEL.items():
    PLATFORM_CHANNELS[_target].append(_source)


@dataclass(frozen=True)
class Band:
    """One bucket of the response-time distribution."""

    key: str
    label: str
    upper_seconds: float
    """Exclusive upper bound; ``inf`` on the last band."""


#: The distribution's buckets, in axis order. "Hybrid" ranges on purpose:
#: minutes resolve the fast end where most replies land, hours the slow tail.
BANDS = [
    Band("lt5m", "< 5 min", 5 * 60),
    Band("m5_15", "5 - 15 min", 15 * 60),
    Band("m15_30", "15 - 30 min", 30 * 60),
    Band("m30_60", "30 - 60 min", 60 * 60),
    Band("h1_4", "1 - 4 h", 4 * 3600),
    Band("h4_24", "4 - 24 h", 24 * 3600),
    Band("gt24", "> 24 h", float("inf")),
]

#: The distribution's two series. Same shape as volumen's Channel palette:
#: validated {light, dark} steps carried by key, never by index. Purple and
#: orange match the KPI tiles they summarize -- the tile's border, the line's
#: swatch and the tooltip row all say "this is the same measure".
SERIES = [
    {"key": "promedio", "label": "Tiempo promedio", "light": "#7c5cd6", "dark": "#a78bfa"},
    {"key": "postmia", "label": "Post-MIA", "light": "#e8590c", "dark": "#fb923c"},
]


# --- Filters ----------------------------------------------------------------


def parse_agent(params):
    """Read ``?agent=`` off a QueryDict -> an active User, or None.

    Anything unknown or malformed means "Todos los agentes" rather than an
    error, matching parse_range's stance.
    """
    raw = params.get("agent", "")
    if not raw:
        return None
    try:
        return get_user_model().objects.filter(is_active=True).get(pk=int(raw))
    except (ValueError, get_user_model().DoesNotExist):
        return None


def parse_platform(params) -> str:
    """Read ``?platform=`` -> a PLATFORMS key, or "" for all of them."""
    raw = params.get("platform", "")
    return raw if raw in PLATFORM_BY_KEY else ""


# --- Formatting -------------------------------------------------------------


def format_duration(seconds: float) -> str:
    """A duration as the tiles print it: ``0 hr 0 min 0 s``.

    Hours accumulate rather than rolling into days -- "27 hr" reads as a
    response-time problem at a glance, which is the tile's whole job.
    """
    total = int(round(seconds))
    return f"{total // 3600} hr {total % 3600 // 60} min {total % 60} s"


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


# --- The pairing walk -------------------------------------------------------


def _collect(lower, upper, platform: str) -> tuple[list, list]:
    """Walk the period's messages once and return (responses, escalations).

    Each response is ``(gap_seconds, responder_id, conversation_id)``; each
    escalation is ``(gap_seconds, responder_id, conversation_id)`` too, where
    the responder is the human who stepped in.

    Streamed with ``iterator()`` -- the state below is three variables per
    conversation, never the period's rows.
    """
    rows = Message.objects.filter(timestamp__gte=lower, timestamp__lt=upper)
    if platform:
        rows = rows.filter(conversation__channel__in=PLATFORM_CHANNELS[platform])
    rows = rows.order_by("conversation_id", "timestamp", "id").values_list(
        "conversation_id", "direction", "timestamp", "sent_by_id"
    )

    responses, escalations = [], []
    current = None          # conversation the state below belongs to
    waiting_since = None    # first inbound of the unanswered run, or None
    prev_outbound = None    # (timestamp, sent_by_id) if the previous row was outbound

    for conversation_id, direction, timestamp, sent_by_id in rows.iterator():
        if conversation_id != current:
            current, waiting_since, prev_outbound = conversation_id, None, None

        if direction == Message.INBOUND:
            if waiting_since is None:
                waiting_since = timestamp
            prev_outbound = None
            continue

        if waiting_since is not None:
            gap = (timestamp - waiting_since).total_seconds()
            responses.append((gap, sent_by_id, conversation_id))
            waiting_since = None
        if prev_outbound is not None and prev_outbound[1] is None and sent_by_id is not None:
            gap = (timestamp - prev_outbound[0]).total_seconds()
            escalations.append((gap, sent_by_id, conversation_id))
        prev_outbound = (timestamp, sent_by_id)

    return responses, escalations


# --- Report -----------------------------------------------------------------


def _counts(gaps: list[float]) -> list[int]:
    """How many of ``gaps`` fall in each band, in BANDS order."""
    counts = [0] * len(BANDS)
    for gap in gaps:
        for index, band in enumerate(BANDS):
            if gap < band.upper_seconds:
                counts[index] += 1
                break
    return counts


def _distribution(counts: list[int]) -> list[int]:
    """Band counts as percentages.

    Plain rounding per band: the bars answer "where do most replies land",
    and forcing the row to sum to exactly 100 would misprint some band by a
    point to fix an error nobody reads off a bar chart.
    """
    total = sum(counts)
    if not total:
        return [0] * len(BANDS)
    return [round(count * 100 / total) for count in counts]


#: The three tiles: (key, label, tone, tooltip). Two measure speed, the
#: third counts what was measured -- the template splits them into the
#: "Tiempos de respuesta" and "Resumen" groups on that line.
TILES = [
    (
        "avg",
        "Tiempo promedio",
        "purple",
        "Promedio de lo que esperó un cliente desde su primer mensaje sin "
        "responder hasta la siguiente respuesta de tu cuenta.",
    ),
    (
        "postmia",
        "Post-MIA promedio",
        "orange",
        "Cuando una automatización respondió primero y luego escribió una "
        "persona, este es el promedio que tardó esa persona en intervenir.",
    ),
    (
        "measured",
        "Conversaciones medidas",
        "blue",
        "Conversaciones del período con al menos una respuesta medida, "
        "aplicando los filtros de agente y plataforma.",
    ),
]


def _tiles(response_gaps, escalation_gaps, conversations, agent) -> list[dict]:
    """The KPI tiles with their numbers already formatted -- same stance as
    volumen's: one set of strings for the first render and every re-fetch."""
    n_escalations = len(escalation_gaps)
    values = {
        "avg": (
            format_duration(
                sum(response_gaps) / len(response_gaps) if response_gaps else 0
            ),
            f"Respuestas de {agent.username}" if agent else "Todas las conversaciones",
        ),
        "postmia": (
            format_duration(
                sum(escalation_gaps) / n_escalations if n_escalations else 0
            ),
            f"{format_number(n_escalations)} "
            f"{_plural(n_escalations, 'escalación', 'escalaciones')} en el período",
        ),
        "measured": (format_number(len(conversations)), "en el período"),
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


def _build(start: date, end: date, agent, platform: str) -> dict:
    """The uncached report. See :func:`report` for the caching wrapper."""
    lower, upper = _window(start, end)
    responses, escalations = _collect(lower, upper, platform)

    if agent is not None:
        responses = [r for r in responses if r[1] == agent.pk]
        escalations = [e for e in escalations if e[1] == agent.pk]

    response_gaps = [gap for gap, _, _ in responses]
    escalation_gaps = [gap for gap, _, _ in escalations]
    measured = {conversation for _, _, conversation in responses}

    counts = [_counts(response_gaps)]
    series = [dict(SERIES[0], values=_distribution(counts[0]))]
    # Like a silent channel in volumen: no escalations, no second series --
    # and "Tiempo promedio" keeps its purple either way.
    if escalation_gaps:
        counts.append(_counts(escalation_gaps))
        series.append(dict(SERIES[1], values=_distribution(counts[1])))

    table = [
        {
            "label": band.label,
            "values": [
                f"{format_number(counts[column][index])} ({series[column]['values'][index]}%)"
                for column in range(len(series))
            ],
        }
        for index, band in enumerate(BANDS)
    ]

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "range_label": format_range(start, end),
        "agent": str(agent.pk) if agent else "",
        "platform": platform,
        "tiles": _tiles(response_gaps, escalation_gaps, measured, agent),
        "bands": [band.label for band in BANDS],
        "series": series,
        "table": table,
        "measured": len(measured),
        "responses": len(response_gaps),
    }


def report(start: date, end: date, agent, platform: str) -> dict:
    """Everything the screen needs for one (period, filters), cached for
    :data:`CACHE_SECONDS` -- same freshness trade as volumen's report."""
    agent_key = agent.pk if agent else ""
    key = (
        f"stats:tiempos:{start.isoformat()}:{end.isoformat()}"
        f":{agent_key}:{platform}"
    )
    cached = cache.get(key)
    if cached is not None:
        return cached
    built = _build(start, end, agent, platform)
    cache.set(key, built, CACHE_SECONDS)
    return built
