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


#: Providers that exist only for local development. They mint contacts,
#: conversations and messages straight out of the request body, with no real
#: account behind them -- see :func:`is_enabled_provider`.
_DEV_ONLY = frozenset({FakeProvider.name})


def is_enabled_provider(name: str) -> bool:
    """Whether ``name`` may answer a webhook in *this* environment.

    Real providers stay routable even when another one is active: a Twilio
    status callback for a message sent last week must still parse as Twilio
    halfway through a migration to Meta, which is why the webhook URL names
    the provider instead of reading the setting.

    The fake provider is the exception. It invents whatever the request body
    says, so on a deployment running a real provider its endpoint is simply
    an unauthenticated writer into the production database -- and the rows it
    creates are indistinguishable from real customers afterwards. It answers
    only where it is itself the configured provider.
    """
    if not is_known_provider(name):
        return False
    if name in _DEV_ONLY:
        return settings.MESSAGING_PROVIDER == name
    return True
