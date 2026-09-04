"""The webhook endpoint's fail-closed rules.

Two holes these cover, both of which let anyone on the internet write rows
into the production database that the app then renders as real customers:

* the fake provider stayed routable on a deployment running a real provider,
  and its shared secret shipped with a value committed to the repository;
* the sidecar secret shipped with a committed default too, so the baileys
  slug accepted forged traffic even when it was not the active provider.

The database is shared with an external automation, so an injected row is
indistinguishable from a real conversation once it lands.
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


def url(provider: str) -> str:
    return reverse("messaging_webhook", args=[provider])


class FakeProviderIsDevelopmentOnlyTests(TestCase):
    """The fake provider mints contacts and messages straight out of the
    request body. It may answer only where it is itself configured."""

    def post(self, provider="fake", secret="dev-secret"):
        return self.client.post(
            url(provider),
            data=json.dumps(INBOUND),
            content_type="application/json",
            headers={"X-Fake-Signature": secret},
        )

    @override_settings(MESSAGING_PROVIDER="meta", MESSAGING_FAKE_SECRET="dev-secret")
    def test_fake_webhook_is_gone_when_a_real_provider_is_active(self):
        response = self.post()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Client.objects.count(), 0)

    @override_settings(MESSAGING_PROVIDER="meta")
    def test_fake_handshake_is_gone_too(self):
        # Otherwise it reflects hub.challenge unauthenticated on the
        # production origin.
        response = self.client.get(url("fake"), {"hub.challenge": "<script>x</script>"})

        self.assertEqual(response.status_code, 404)

    @override_settings(MESSAGING_PROVIDER="fake", MESSAGING_FAKE_SECRET="dev-secret")
    def test_fake_webhook_still_works_in_development(self):
        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.count(), 1)

    @override_settings(MESSAGING_PROVIDER="fake", MESSAGING_FAKE_SECRET="")
    def test_an_unset_secret_rejects_instead_of_waving_traffic_through(self):
        response = self.post(secret="")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(MESSAGING_PROVIDER="fake")
    def test_the_handshake_never_reflects_as_html(self):
        response = self.client.get(url("fake"), {"hub.challenge": "abc123"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"abc123")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))


class SidecarSecretTests(TestCase):
    """baileys is a real provider, so it stays routable mid-migration -- which
    makes a committed default secret an open door rather than a convenience."""

    def post(self, secret=""):
        return self.client.post(
            url("baileys"),
            data=json.dumps(INBOUND),
            content_type="application/json",
            headers={"X-Sidecar-Secret": secret},
        )

    @override_settings(MESSAGING_PROVIDER="meta", BAILEYS_SIDECAR_SECRET="")
    def test_an_unset_sidecar_secret_rejects_everything(self):
        for supplied in ["", "dev-sidecar-secret", "anything"]:
            with self.subTest(supplied):
                self.assertEqual(self.post(supplied).status_code, 401)
        self.assertEqual(Message.objects.count(), 0)

    @override_settings(MESSAGING_PROVIDER="baileys", BAILEYS_SIDECAR_SECRET="real-secret")
    def test_a_configured_sidecar_secret_still_works(self):
        self.assertEqual(self.post("real-secret").status_code, 200)
        self.assertEqual(Message.objects.count(), 1)
