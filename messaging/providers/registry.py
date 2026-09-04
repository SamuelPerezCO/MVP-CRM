"""Which provider is live, decided by settings alone.

Swapping fake -> Twilio -> Meta is a one-line change to
``MESSAGING_PROVIDER`` (env var / settings); no code imports a concrete
provider class except this module and the tests.
"""

from __future__ import annotations

from django.conf import settings

from .base import MessagingProvider
from .fake import FakeProvider
from .meta import MetaProvider
from .twilio import TwilioProvider

_PROVIDERS: dict[str, type[MessagingProvider]] = {
    FakeProvider.name: FakeProvider,
    TwilioProvider.name: TwilioProvider,
    MetaProvider.name: MetaProvider,
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
