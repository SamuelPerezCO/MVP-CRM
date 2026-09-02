"""
The shared period select for Estadísticas *panels* (Temas de conversación,
Ventas): a small fixed menu of relative windows.

Deliberately not the volumen/tiempos date-range picker: those are card
detail screens with their own JS and JSON feeds, while panels swap
wholesale over HTMX -- a fixed menu keeps the control stateless (the
re-rendered select carries its own selection) and every option
bookmarkable as ``?period=<key>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone


@dataclass(frozen=True)
class Period:
    """One option in the period select."""

    key: str
    """Value in the URL / select option."""

    label: str
    """Visible Spanish label."""

    days: int | None
    """Window counting back from now; ``None`` means all history."""


#: Order here is the order rendered in the select.
PERIODS = [
    Period("7", "Últimos 7 días", 7),
    Period("30", "Últimos 30 días", 30),
    Period("90", "Últimos 90 días", 90),
    Period("todo", "Todo el historial", None),
]

PERIOD_BY_KEY = {period.key: period for period in PERIODS}

DEFAULT_PERIOD = "30"


def parse_period(data) -> Period:
    """The ``?period=`` value out of a GET QueryDict, falling back to the
    default rather than erroring -- a stale bookmark still opens."""
    return PERIOD_BY_KEY.get(data.get("period", ""), PERIOD_BY_KEY[DEFAULT_PERIOD])


def start_of(period: Period) -> datetime | None:
    """The window's lower bound, or ``None`` for all history."""
    if period.days is None:
        return None
    return timezone.now() - timedelta(days=period.days)
