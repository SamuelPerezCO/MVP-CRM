"""Empty the Inbox: delete every conversation, message and contact.

The counterpart to ``seed_conversations``. Written for the moment before a
demo, when the Inbox holds a mix of seeded fixtures and your own test
messages and you want a clean slate that fills up with real customers only.

Two deliberate safety choices, because this runs against a *production*
database and there is no undo:

* It is a **dry run by default**. Without ``--yes`` it prints the database it
  is pointed at and what it would delete, and changes nothing. Naming the
  host matters: DATABASE_URL is easy to point at the wrong project, and
  "which database did I just wipe" is a bad question to ask afterwards.
* The delete runs in one transaction, so a failure halfway leaves the Inbox
  as it was rather than half-erased.

Tags, message templates, client lists, calendar events and agents all
survive -- this empties conversations and the contacts they belong to,
nothing else. ``CalendarEvent.contact`` is SET_NULL, so events outlive the
contact they referenced.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from core.models import Client
from messaging.models import Conversation, ConversationTag, Message


class Command(BaseCommand):
    help = (
        "Delete every conversation, message and contact -- an empty Inbox. "
        "Dry run unless --yes is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually delete. Without this the command only reports.",
        )
        parser.add_argument(
            "--keep-clients",
            action="store_true",
            help=(
                "Delete conversations and messages but keep the contact "
                "records, so client history survives an Inbox reset."
            ),
        )

    def handle(self, *args, **options):
        confirmed = options["yes"]
        keep_clients = options["keep_clients"]

        counts = {
            "mensajes": Message.objects.count(),
            "etiquetas de conversación": ConversationTag.objects.count(),
            "conversaciones": Conversation.objects.count(),
            "contactos": Client.objects.count(),
        }
        if keep_clients:
            counts.pop("contactos")

        # Name the target before touching it -- see the module docstring.
        db = connection.settings_dict
        target = db.get("HOST") or db.get("NAME")
        self.stdout.write(f"Base de datos: {db['ENGINE'].split('.')[-1]} · {target}")
        for label, count in counts.items():
            self.stdout.write(f"  {count:>6}  {label}")

        if not any(counts.values()):
            self.stdout.write(self.style.SUCCESS("Nada que borrar: el Inbox ya está vacío."))
            return

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    "\nSimulación: no se borró nada. Vuelve a correr con --yes "
                    "para borrarlo de verdad."
                )
            )
            return

        with transaction.atomic():
            # Explicit order rather than leaning on cascade, so the numbers
            # reported are the numbers actually deleted.
            Message.objects.all().delete()
            ConversationTag.objects.all().delete()
            Conversation.objects.all().delete()
            if not keep_clients:
                Client.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("\nInbox vacío."))
        for label, count in counts.items():
            self.stdout.write(self.style.SUCCESS(f"  {count:>6}  {label} eliminados"))
