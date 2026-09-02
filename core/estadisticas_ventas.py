"""
Data layer for the Estadísticas > Ventas panel: what counts as a sale, and
the report behind the tiles and the per-channel table.

Same placement rationale as the other report modules: ``core.estadisticas``
is the section's nav definition; each built-out page keeps its report in a
sibling.

What "venta" means here, honestly: the app has no order/checkout model, so
a sale is the thing the team can actually record today -- a conversation
tagged with a *sale tag*, any tag whose name contains
:data:`SALE_NAME_TOKEN` («VENTA EFECTIVA», «Venta mayorista»...). The
panel shows which tags count, and its empty state teaches the workflow
when no such tag exists yet. Sales are dated by ``ConversationTag.tagged_at``
(the through table's audit trail), not by the conversation's age: the sale
happened when someone marked it.

When a real sales pipeline lands (orders, amounts, stages), this module is
the one thing it replaces -- the same stance ``estadisticas_temas`` takes
toward an AI classifier.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count

from messaging.models import Conversation, ConversationTag, Tag

from .estadisticas_periodos import Period, start_of

#: A tag whose name contains this (case-insensitively) marks its
#: conversations as sales. A convention rather than a setting, on purpose:
#: the page states it, the empty state teaches it, and there is no
#: configuration screen to build or explain.
SALE_NAME_TOKEN = "venta"


def sale_tags():
    """Every tag that counts as a sale marker, archived ones included --
    an archived «VENTA 2025» still dates the sales it recorded."""
    return Tag.objects.filter(name__icontains=SALE_NAME_TOKEN)


@dataclass(frozen=True)
class ChannelRow:
    """One row of the per-channel table."""

    label: str
    sales: int


#: Conversation channel key -> visible label, for the table rows.
_CHANNEL_LABEL = dict(Conversation.CHANNEL_CHOICES)


def report(period: Period) -> dict:
    """The whole panel's data for one period.

    A conversation carrying two sale tags is one sale, not two -- counts are
    over distinct conversations. The conversion rate divides the period's
    sales by the conversations *created* in the same period; the two sets
    overlap imperfectly (a chat opened in March can close in April), which
    for a rolling window is the honest simple answer, and the tile's
    info-dot says exactly what it divides.
    """
    tags = list(sale_tags())
    tag_ids = [tag.pk for tag in tags]
    start = start_of(period)

    applications = ConversationTag.objects.filter(tag_id__in=tag_ids)
    total_sales = applications.values("conversation_id").distinct().count()
    if start is not None:
        applications = applications.filter(tagged_at__gte=start)

    sale_conversation_ids = set(
        applications.values_list("conversation_id", flat=True)
    )

    conversations_started = Conversation.objects.all()
    if start is not None:
        conversations_started = conversations_started.filter(created_at__gte=start)
    started = conversations_started.count()

    by_channel = (
        Conversation.objects.filter(pk__in=sale_conversation_ids)
        .values("channel")
        .annotate(sales=Count("id"))
    )
    channels = sorted(
        (
            ChannelRow(_CHANNEL_LABEL.get(row["channel"], row["channel"]), row["sales"])
            for row in by_channel
        ),
        key=lambda row: (-row.sales, row.label),
    )

    sales = len(sale_conversation_ids)
    return {
        "sale_tags": tags,
        "sales": sales,
        "conversations_started": started,
        # None renders as an em dash -- 0/0 is "nothing to measure", not "0%".
        "conversion_pct": round(100 * sales / started) if started else None,
        "total_sales": total_sales,
        "channels": channels,
        "max_channel_sales": channels[0].sales if channels else 0,
    }
