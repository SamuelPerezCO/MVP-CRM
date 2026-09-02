"""
Data layer for the Estadísticas > Embudos panel: the conversation funnel.

Same placement rationale as the other report modules: ``core.estadisticas``
is the section's nav definition; each built-out page keeps its report in a
sibling.

What "embudo" means here, honestly: there is no Funnel model yet
(``core.embudos.get_funnels`` returns nothing until the builder exists), so
this page measures the one funnel the data already contains -- the life of
a conversation. Four stages over the period's conversations:

* **Creadas** -- every conversation opened in the period.
* **Respondidas** -- those with at least one outbound message ever.
* **Resueltas** -- those whose *current* status is Resuelta.
* **Con venta** -- those carrying a sale tag (the same «venta»-in-the-name
  convention as ``core.estadisticas_ventas``).

Each stage is measured independently against the period's conversations,
not nested inside the previous stage: a chat can be marked venta without
ever being resuelta. In practice the counts shrink stage by stage, but by
data, not by construction -- the honest shape. When custom funnels land,
they render alongside (or instead of) this built-in one.
"""

from __future__ import annotations

from dataclasses import dataclass

from messaging.models import Conversation, Message

from .estadisticas_periodos import Period, start_of
from .estadisticas_ventas import sale_tags


@dataclass(frozen=True)
class Stage:
    """One bar of the funnel."""

    key: str
    label: str

    count: int

    pct: int
    """Share of the period's conversations, 0-100. The first stage is the
    base, so it is always 100 (when anything exists at all)."""

    tone: str
    """CSS tone suffix -- the same palette the KPI tiles use."""

    tip: str
    """The stage's exact rule, rendered as its info-dot."""


def report(period: Period) -> dict:
    """The whole panel's data for one period."""
    start = start_of(period)
    conversations = Conversation.objects.all()
    if start is not None:
        conversations = conversations.filter(created_at__gte=start)

    base = conversations.count()
    answered = (
        conversations.filter(messages__direction=Message.OUTBOUND).distinct().count()
    )
    resolved = conversations.filter(status=Conversation.RESOLVED).count()
    sold = conversations.filter(tags__in=sale_tags()).distinct().count()

    def pct(count: int) -> int:
        return round(100 * count / base) if base else 0

    stages = [
        Stage(
            "creadas",
            "Conversaciones creadas",
            base,
            100 if base else 0,
            "blue",
            "Toda conversación abierta en el período, en cualquier canal.",
        ),
        Stage(
            "respondidas",
            "Respondidas por el equipo",
            answered,
            pct(answered),
            "teal",
            "Conversaciones del período con al menos un mensaje enviado "
            "por un agente.",
        ),
        Stage(
            "resueltas",
            "Resueltas",
            resolved,
            pct(resolved),
            "green",
            "Conversaciones del período cuyo estado actual es «Resuelta».",
        ),
        Stage(
            "venta",
            "Con venta",
            sold,
            pct(sold),
            "purple",
            "Conversaciones del período etiquetadas como venta -- cualquier "
            "etiqueta con «venta» en el nombre, como en Estadísticas > Ventas.",
        ),
    ]

    return {"stages": stages, "base": base}
