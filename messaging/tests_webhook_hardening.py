"""The webhook endpoint's fail-closed rules.

The hole these cover let anyone on the internet write rows into the
production database that the app then renders as real customers: the fake
provider stayed routable on a deployment running a real provider, and its
shared secret shipped with a value committed to the repository.

The database is shared with an external automation, so an injected row is
indistinguishable from a real conversation once it lands.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Client
from messaging.models import Conversation, Message
from messaging.providers import registry

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




class ConfiguredProviderTests(TestCase):
    """MESSAGING_PROVIDER must name a provider that exists.

    An unknown value used to pass startup and raise only when something tried
    to send, so a deployment looked healthy while every outbound message
    crashed. That matters most right after a provider is dropped, when an
    environment can still be carrying its name.
    """

    def test_the_settings_list_matches_the_registry(self):
        """Two places name the providers; drift between them is the bug this
        catches (settings cannot import the registry at settings time)."""
        self.assertEqual(
            sorted(settings.MESSAGING_PROVIDERS), sorted(registry._PROVIDERS)
        )

    def test_a_provider_this_app_lacks_is_not_known(self):
        self.assertFalse(registry.is_known_provider("retirado"))

    def test_its_webhook_slug_is_a_404(self):
        response = self.client.post(
            "/webhooks/messaging/retirado/", data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
