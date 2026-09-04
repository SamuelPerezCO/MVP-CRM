"""Which provider is live, decided by settings alone.

Swapping fake -> Twilio -> Meta is a one-line change to
``MESSAGING_PROVIDER`` (env var / settings); no code imports a concrete
provider class except this module and the tests.
"""

from __future__ import annotations

from django.conf import settings

from .base import MessagingProvider
from .baileys import BaileysProvider
from .fake import FakeProvider
from .meta import MetaProvider
from .twilio import TwilioProvider

_PROVIDERS: dict[str, type[MessagingProvider]] = {
    FakeProvider.name: FakeProvider,
    TwilioProvider.name: TwilioProvider,
    MetaProvider.name: MetaProvider,
    BaileysProvider.name: BaileysProvider,
}


def get_provider(name: str | None = None) -> MessagingProvider:
    """The active provider (``settings.MESSAGING_PROVIDER``), or a named one.

    The explicit ``name`` form exists for the webhook URL, which addresses a
    provider by slug: a Twilio status callback must be parsed as Twilio even
    if the app is mid-migration to Meta.
    """
    key = name or settings.MESSAGING_PROVIDER
    try:
        return _PROVIDERS[key]()
    except KeyError:
        raise ValueError(
            f"Unknown messaging provider {key!r}; expected one of {sorted(_PROVIDERS)}"
        ) from None


def is_known_provider(name: str) -> bool:
    return name in _PROVIDERS


def webhook_enabled(name: str) -> bool:
    """May this provider's webhook answer on this deployment?

    Every provider's webhook is a door into the database: it creates contacts,
    conversations and messages, exempt from the login gate and from CSRF,
    trusting a signature instead. For the real providers that signature is a
    credential only Meta/Twilio/the sidecar holds. For the *fake* one it is
    ``MESSAGING_FAKE_SECRET``, whose default (``dev-secret``) is published in
    ``.env.example`` and the README -- so on a deployment that never set it,
    anyone who knows this project could POST invented customers into a live
    Inbox.

    So the fake webhook answers only where fake data belongs: local
    development and the test runner. ``MESSAGING_ALLOW_FAKE_WEBHOOK=True``
    re-opens it for a staging deployment that wants the simulator.
    """
    if name != FakeProvider.name:
        return True
    return bool(
        settings.DEBUG
        or getattr(settings, "TESTING", False)
        or getattr(settings, "MESSAGING_ALLOW_FAKE_WEBHOOK", False)
    )
