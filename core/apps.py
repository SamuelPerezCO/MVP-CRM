from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # Registers core.checks.plaintext_env_secrets (core.W001).
        from . import checks  # noqa: F401
