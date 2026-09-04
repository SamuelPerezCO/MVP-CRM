"""Compare what the CRM billed itself against what Meta says it charged.

    python manage.py meta_spend                # this calendar month
    python manage.py meta_spend --month 2026-08

The CRM keeps its own ledger: a price frozen on every template send, then
corrected by Meta's delivery receipt. That is per-message and can only see
messages whose receipt arrived. This sweep asks Meta for the account's own
figures over a whole window and reports the difference, which is how a
missing webhook, a message sent outside this CRM, or a rate the CRM has wrong
becomes visible.

Two honest caveats, both printed by the command rather than buried here:

* Meta describes analytics cost as *approximate*. The invoice is the record
  of truth; this is a check, not an accounting system.
* Meta withholds cost entirely from an account billed through a solution
  partner's credit line. Then every data point arrives without a cost and
  the command says so instead of reporting zero.

Needs MESSAGING_PROVIDER=meta, META_WABA_ID and a token with
whatsapp_business_management.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from messaging import pricing
from messaging.providers.registry import get_provider


class Command(BaseCommand):
    help = "Compare the CRM's template spend with Meta's own pricing analytics."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            help="Calendar month to sweep, as YYYY-MM. Defaults to this month.",
        )

    def handle(self, *args, **options):
        start, end = self._window(options.get("month"))

        provider = get_provider()
        fetch = getattr(provider, "fetch_pricing_analytics", None)
        if fetch is None:
            raise CommandError(
                f"the {provider.name!r} provider has no billing analytics -- "
                "set MESSAGING_PROVIDER=meta"
            )

        try:
            account = provider.fetch_account()
            points = fetch(start, end, granularity="DAILY")
        except Exception as exc:
            raise CommandError(f"could not read Meta's analytics: {exc}") from exc

        currency = account.get("currency") or "?"
        meta_totals = pricing.meta_spend_by_category(points)
        bookkeeping = meta_totals.pop("_meta")
        crm_totals = pricing.crm_spend_by_category(start, end)

        self.stdout.write(
            f"{start:%Y-%m-%d} to {end:%Y-%m-%d} · WABA {account.get('id', '?')} "
            f"· {currency}"
        )

        # Cost suppressed for partner-billed accounts: say so plainly, and do
        # not let a total of zero read as "nothing was spent".
        if bookkeeping["points_without_cost"]:
            if account.get("is_shared_with_partners"):
                self.stdout.write(
                    self.style.WARNING(
                        "  Meta reports no cost for this account (billed through a "
                        "solution partner). Volume only; ask your BSP for the money."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {bookkeeping['points_without_cost']} data point(s) came "
                        "back without a cost; those are excluded from the totals."
                    )
                )

        # Amounts are Decimals carrying every digit of their arithmetic
        # (0.0125000000000000); a fixed width is only a column if the value
        # is rounded for display first. Four places matches the rate card.
        def money(value):
            return f"{Decimal(value):>14.4f}"

        categories = sorted(set(meta_totals) | set(crm_totals))
        if not categories:
            # "Nothing billed" is only true when nothing moved at all. An
            # account whose costs Meta withholds still delivered messages,
            # and saying nothing was billed there would be the wrong end of
            # the same mistake this command exists to catch.
            if bookkeeping["volume"]:
                self.stdout.write(
                    f"  {bookkeeping['volume']} mensajes entregados según Meta, "
                    "sin costo informado."
                )
            else:
                self.stdout.write("  Nothing billed in this window.")
            return

        self.stdout.write(f"  {'categoría':<28}{'Meta':>14}{'CRM':>14}{'dif.':>14}")
        meta_sum = crm_sum = 0
        for category in categories:
            meta_amount = meta_totals.get(category, 0)
            crm_amount = crm_totals.get(category, 0)
            meta_sum += meta_amount
            crm_sum += crm_amount
            line = (
                f"  {category or '(sin categoría)':<28}"
                f"{money(meta_amount)}{money(crm_amount)}"
                f"{money(meta_amount - crm_amount)}"
            )
            # A difference is the whole point of the sweep, so it is coloured.
            self.stdout.write(
                self.style.WARNING(line) if meta_amount != crm_amount else line
            )
        self.stdout.write(
            f"  {'total':<28}{money(meta_sum)}{money(crm_sum)}"
            f"{money(meta_sum - crm_sum)}"
        )
        self.stdout.write(
            f"  {bookkeeping['volume']} mensajes entregados según Meta. "
            "Las cifras de Meta son aproximadas; la factura manda."
        )

    def _window(self, month: str | None):
        """The [start, end) the sweep covers, in the active timezone."""
        if not month:
            start = pricing.month_start()
        else:
            try:
                parsed = datetime.strptime(month, "%Y-%m")
            except ValueError as exc:
                raise CommandError("--month must look like 2026-08") from exc
            start = timezone.make_aware(parsed)
        # First day of the next month: add enough days to land in it, then
        # snap back to the 1st. Avoids month-length arithmetic.
        end = (start + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0,
                                                   microsecond=0)
        return start, end
