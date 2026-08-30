"""Push a fake inbound message through the real webhook code path.

    python manage.py simulate_inbound "+573000000001" "Hola, ¿sigue disponible?"

Builds the fake provider's webhook payload, signs it, and calls the actual
webhook view -- signature check, parsing, idempotency, conversation upsert
and all. With ``runserver`` open on the Inbox, the message lands in the UI
on the next poll (a few seconds), which is exactly how a real provider's
webhook will behave.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from django.utils import timezone

from messaging.views import webhook


class Command(BaseCommand):
    help = "Simulate an inbound message arriving through the fake provider's webhook."

    def add_arguments(self, parser):
        parser.add_argument("phone", help='Sender in E.164, e.g. "+573000000001"')
        parser.add_argument("message", help="The message text")
        parser.add_argument(
            "--name",
            default="",
            help="Contact display name, used only if the phone is new to the CRM",
        )
        parser.add_argument(
            "--channel",
            default="whatsapp",
            help="Conversation channel key (default: whatsapp)",
        )
        parser.add_argument(
            "--bad-signature",
            action="store_true",
            help="Send a wrong signature instead, to watch the 401 rejection",
        )

    def handle(self, *args, **options):
        payload = {
            "events": [
                {
                    "event_type": "message",
                    "from_number": options["phone"],
                    "body": options["message"],
                    "channel": options["channel"],
                    "contact_name": options["name"],
                    "timestamp": timezone.now().isoformat(),
                }
            ]
        }
        signature = (
            "wrong" if options["bad_signature"] else settings.MESSAGING_FAKE_SECRET
        )

        # The same view the URL routes to -- not a shortcut around it. Using
        # RequestFactory keeps this working without a server running.
        request = RequestFactory().post(
            "/webhooks/messaging/fake/",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Fake-Signature": signature},
        )
        response = webhook(request, provider_name="fake")

        if response.status_code != 200:
            raise CommandError(
                f"Webhook answered {response.status_code}: "
                f"{response.content.decode()}"
            )
        self.stdout.write(self.style.SUCCESS(
            f"Inbound message from {options['phone']} accepted "
            f"(webhook answered 200). Watch it land in the Inbox."
        ))
