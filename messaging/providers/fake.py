"""A provider that behaves like WhatsApp without leaving the machine.

Used while no real credentials exist (``MESSAGING_PROVIDER=fake``, the
default). It keeps the whole pipeline honest:

* ``send_text`` returns a message id like a real API would.
* Delivery receipts happen: previously-sent messages advance through
  sent -> delivered -> read over a few seconds, surfaced as the same
  ``status`` events a real provider would POST to the webhook -- so the tick
  marks in the UI move for real, through the real code path.
* Webhooks are signed: requests must carry ``X-Fake-Signature`` matching
  ``settings.MESSAGING_FAKE_SECRET``, so the 401 branch is exercised too.

The ``simulate_inbound`` management command POSTs this provider's payload
shape at the webhook endpoint to fake a customer writing in.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from .base import MessagingProvider
from .types import InboundEvent, MessageStatus, status_rank

logger = logging.getLogger(__name__)

#: Seconds after sending at which each receipt becomes due. One step is
#: released per poll (see ``pending_status_events``), so with the UI polling
#: every 5s the ticks visibly progress instead of jumping straight to read.
_STATUS_DELAYS = [
    (MessageStatus.SENT, 2),
    (MessageStatus.DELIVERED, 5),
    (MessageStatus.READ, 10),
]


class FakeProvider(MessagingProvider):
    name = "fake"

    # --- Sending -----------------------------------------------------------

    def send_text(self, to: str, body: str) -> str:
        message_id = f"fake-{uuid.uuid4().hex}"
        logger.info("[fake] send_text to=%s id=%s body=%r", to, message_id, body)
        return message_id

    def send_template(self, to: str, template_name: str, params: dict) -> str:
        message_id = f"fake-{uuid.uuid4().hex}"
        logger.info(
            "[fake] send_template to=%s id=%s template=%s params=%r",
            to, message_id, template_name, params,
        )
        return message_id

    # --- Webhook -----------------------------------------------------------

    def verify_signature(self, request) -> bool:
        """A shared secret in ``X-Fake-Signature``.

        Deliberately simpler than the real providers' HMACs, but real enough
        that the endpoint's reject-before-parse branch gets exercised.
        """
        supplied = request.headers.get("X-Fake-Signature", "")
        return constant_time_compare(supplied, settings.MESSAGING_FAKE_SECRET)

    def parse_webhook(self, request) -> list[InboundEvent]:
        """Payload shape: ``{"events": [{...InboundEvent fields...}]}``.

        Field names match :class:`InboundEvent` one-to-one -- this provider
        has no legacy payload to translate, so it doesn't invent one.
        """
        try:
            payload = json.loads(request.body)
            raw_events = payload["events"]
        except (ValueError, KeyError) as exc:
            raise ValueError(f"unparseable fake webhook payload: {exc}") from exc

        events = []
        for raw in raw_events:
            timestamp = None
            if raw.get("timestamp"):
                timestamp = datetime.fromisoformat(raw["timestamp"])
                if timezone.is_naive(timestamp):
                    timestamp = timezone.make_aware(timestamp)
            events.append(
                InboundEvent(
                    event_type=raw.get("event_type", "message"),
                    provider_message_id=raw.get("provider_message_id")
                    or f"fake-in-{uuid.uuid4().hex}",
                    from_number=raw.get("from_number", ""),
                    to_number=raw.get("to_number", ""),
                    body=raw.get("body", ""),
                    media_url=raw.get("media_url", ""),
                    media_type=raw.get("media_type", ""),
                    timestamp=timestamp,
                    status=MessageStatus(raw["status"]) if raw.get("status") else None,
                    channel=raw.get("channel", "whatsapp"),
                    contact_name=raw.get("contact_name", ""),
                )
            )
        return events

    def handshake(self, request) -> str | None:
        """Echo ``hub.challenge`` like Meta does, so the GET handshake path
        can be tried end-to-end before real credentials exist."""
        return request.GET.get("hub.challenge")

    # --- Delivery simulation ----------------------------------------------

    def pending_status_events(self) -> list[InboundEvent]:
        """Receipts that have become due for messages this provider "sent".

        A real provider POSTs these to the webhook on its own; the fake one is
        pull-based instead: the UI's poll endpoints call this (via
        ``services.pump_provider_events``) and feed the result through the
        same event processing as a webhook.

        State lives in the ``Message`` table itself -- each message's status
        and age say which receipt is due next -- so it survives dev-server
        restarts and works across processes (``manage.py`` commands vs.
        ``runserver``). At most one step per message per call, so transitions
        stay visible in the UI instead of collapsing into one jump.
        """
        # App-model import kept out of module scope: providers load with
        # settings, before the app registry is necessarily ready.
        from messaging.models import Message

        now = timezone.now()
        events = []
        candidates = Message.objects.filter(
            direction=Message.OUTBOUND,
            provider_message_id__startswith="fake-",
            status__in=[
                MessageStatus.QUEUED.value,
                MessageStatus.SENT.value,
                MessageStatus.DELIVERED.value,
            ],
        )
        for message in candidates:
            age = now - message.timestamp
            for status, delay in _STATUS_DELAYS:
                # Only steps *beyond* the current status count -- comparing by
                # rank, not equality, so a delivered message advances to read
                # rather than re-emitting sent.
                is_next_step = status_rank(status.value) > status_rank(message.status)
                if is_next_step and age >= timedelta(seconds=delay):
                    events.append(
                        InboundEvent(
                            event_type="status",
                            provider_message_id=message.provider_message_id,
                            status=status,
                            timestamp=now,
                            channel=message.conversation.channel,
                        )
                    )
                    break  # one step per call; the next poll takes the next one
        return events
