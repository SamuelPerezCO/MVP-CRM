"""Provider integrations for the messaging layer.

One module per provider, all implementing :class:`base.MessagingProvider`.
The rest of the app only ever talks to :func:`registry.get_provider`, so the
active provider is a deployment setting, not a code path.
"""
