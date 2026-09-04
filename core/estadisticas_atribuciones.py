"""
Data layer for the Estadísticas > Atribuciones panel: which channel the
period's conversations -- and the sales among them -- are attributed to.

Same placement rationale as the other report modules: ``core.estadisticas``
is the section's nav definition; each built-out page keeps its report in a
sibling.

Why this page is «Alpha», honestly: real ad attribution needs referral data
the webhooks don't capture yet (Meta's click-to-WhatsApp payloads carry the
ad and campaign that opened the chat). Until that lands, attribution stops
at the channel: every conversation belongs to exactly one, so the question
«de dónde vienen tus conversaciones y tus ventas» has a complete,
non-speculative answer at that grain. When referral capture arrives, ad
rows slot in under their channel and this module grows -- nothing here is
placeholder math.

The rules line up with the Embudos panel on purpose: conversations are
windowed by ``created_at`` and a sale is a conversation carrying a sale tag
(the «venta»-in-the-name convention from ``core.estadisticas_ventas``), so
this table's totals agree with the funnel's «Creadas» and «Con venta»
stages for the same period.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count

from messaging.models import Conversation

from .estadisticas_periodos import Period, start_of
from .estadisticas_ventas import sale_tags

#: Conversation channel key -> visible label, for the table rows.
_CHANNEL_LABEL = dict(Conversation.CHANNEL_CHOICES)


@dataclass(frozen=True)
class Row:
    """One channel's line in the attribution table."""

    key: str
    label: str

    conversations: int

    sales: int
    """Conversations of this row carrying a sale tag."""

    conversion_pct: int
    """``sales`` as a share of this row's own conversations, 0-100."""

    share_pct: int
    """This row's conversations as a share of the period's total, 0-100."""


def report(period: Period) -> dict:
    """The whole panel's data for one period.

    Only channels with at least one conversation in the window get a row --
    a channel nobody wrote on attributes nothing.
    """
    start = start_of(period)
    conversations = Conversation.objects.all()
    if start is not None:
        conversations = conversations.filter(created_at__gte=start)

    counts = {
        entry["channel"]: entry["n"]
        for entry in conversations.values("channel").annotate(n=Count("id"))
    }
    # distinct=True because a chat carrying two sale tags joins twice.
    sold = {
        entry["channel"]: entry["n"]
        for entry in conversations.filter(tags__in=sale_tags())
        .values("channel")
        .annotate(n=Count("id", distinct=True))
    }

    total = sum(counts.values())
    rows = sorted(
        (
            Row(
                key=channel,
                label=_CHANNEL_LABEL.get(channel, channel),
                conversations=count,
                sales=sold.get(channel, 0),
                conversion_pct=round(100 * sold.get(channel, 0) / count),
                share_pct=round(100 * count / total),
            )
            for channel, count in counts.items()
        ),
        key=lambda row: (-row.conversations, row.label),
    )

    with_sales = [row for row in rows if row.sales]
    return {
        "rows": rows,
        "total_conversations": total,
        "total_sales": sum(sold.values()),
        "max_conversations": rows[0].conversations if rows else 0,
        # The tiles: where most conversations start, and where they close.
        "top": rows[0] if rows else None,
        "best_conversion": max(
            with_sales, key=lambda row: (row.conversion_pct, row.sales), default=None
        ),
    }
