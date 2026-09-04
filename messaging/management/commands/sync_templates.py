"""Pull the WhatsApp Business Account's templates from Meta into the CRM.

    python manage.py sync_templates

Run it after creating or editing plantillas in WhatsApp Manager, and on a
schedule if the account is busy: Meta re-categorises templates on its own
(usually with a day's notice) and that category is what a send is billed at,
so a stale copy quotes the wrong price and can offer a plantilla WhatsApp
would refuse to deliver.

Needs MESSAGING_PROVIDER=meta, META_ACCESS_TOKEN (with the
whatsapp_business_management permission) and META_WABA_ID. The work itself is
``messaging.services.sync_templates``; this is the way to run it by hand.
"""

from django.core.management.base import BaseCommand, CommandError

from messaging import services


class Command(BaseCommand):
    help = "Sync WhatsApp message templates (status and category) from Meta."

    def handle(self, *args, **options):
        try:
            report = services.sync_templates()
        except Exception as exc:
            # A sync that failed must not look like a sync that found
            # nothing: CommandError exits non-zero and prints to stderr.
            raise CommandError(f"template sync failed: {exc}") from exc

        self.stdout.write(
            f"{report['fetched']} plantillas en Meta: "
            f"{report['created']} nuevas, {report['updated']} actualizadas, "
            f"{report['unmatched']} solo en el CRM."
        )
        # The important line: a category change moves the price of every
        # future send of that plantilla, so it is called out rather than
        # buried in the counts.
        for change in report["recategorised"]:
            self.stdout.write(
                self.style.WARNING(
                    f"  Meta recategorizó «{change['name']}»: "
                    f"{change['from']} -> {change['to']} (cambia el precio)"
                )
            )
