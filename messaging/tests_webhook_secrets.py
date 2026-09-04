"""Webhook secrets fail closed.

``registry.webhook_enabled`` shuts the *simulator's* door on a real
deployment. The real providers' doors stay open by design -- a Twilio status
callback must still parse as Twilio halfway through a migration to Meta -- so
for those the shared secret is the only thing in front of the database.

Which makes a secret with a default a secret an attacker already has: both
values used to ship in this repository. baileys is the one that mattered,
being a real provider whose slug answers everywhere. Now an empty secret
rejects everything rather than accepting the published default.
"""

from __future__ import annotations

import json

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Client
from messaging.models import Conversation, Message

INBOUND = {
    "events": [
        {
            "event_type": "message",
            "provider_message_id": "forged-1",
            "from_number": "+573001112233",
            "to_number": "+573000000000",
            "body": "inyectado",
            "contact_name": "Inyectado",
        }
    ]
}


def post(client, provider, header, secret):
    return client.post(
        reverse("messaging_webhook", args=[provider]),
        data=json.dumps(INBOUND),
        content_type="application/json",
        headers={header: secret},
    )


class SidecarSecretTests(TestCase):
    """baileys is real, so its slug answers on every deployment."""

    @override_settings(MESSAGING_PROVIDER="meta", BAILEYS_SIDECAR_SECRET="")
    def test_an_unset_secret_rejects_everything(self):
        for supplied in ["", "dev-sidecar-secret", "anything"]:
            with self.subTest(supplied=supplied):
                response = post(self.client, "baileys", "X-Sidecar-Secret", supplied)
                self.assertEqual(response.status_code, 401)

        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)

    @override_settings(MESSAGING_PROVIDER="baileys", BAILEYS_SIDECAR_SECRET="real-secret")
    def test_a_configured_secret_still_works(self):
        self.assertEqual(
            post(self.client, "baileys", "X-Sidecar-Secret", "real-secret").status_code, 200
        )
        self.assertEqual(Message.objects.count(), 1)

    @override_settings(MESSAGING_PROVIDER="baileys", BAILEYS_SIDECAR_SECRET="real-secret")
    def test_the_wrong_secret_is_still_rejected(self):
        self.assertEqual(
            post(self.client, "baileys", "X-Sidecar-Secret", "guess").status_code, 401
        )


class FakeSecretTests(TestCase):
    @override_settings(MESSAGING_FAKE_SECRET="")
    def test_an_unset_secret_rejects_instead_of_waving_traffic_through(self):
        response = post(self.client, "fake", "X-Fake-Signature", "")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(MESSAGING_FAKE_SECRET="local-secret")
    def test_a_configured_secret_works_in_development(self):
        # TESTING is true here, so registry.webhook_enabled lets it through.
        self.assertEqual(
            post(self.client, "fake", "X-Fake-Signature", "local-secret").status_code, 200
        )
        self.assertEqual(Message.objects.count(), 1)


class HandshakeTests(TestCase):
    """The challenge is whatever the caller sent, so it must never come back
    as HTML on the deployment's own origin."""

    def test_the_challenge_is_returned_as_plain_text(self):
        response = self.client.get(
            reverse("messaging_webhook", args=["fake"]), {"hub.challenge": "abc123"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"abc123")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
