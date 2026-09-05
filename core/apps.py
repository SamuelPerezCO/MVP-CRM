from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # Registers core.checks.plaintext_env_secrets (core.W001) and
        # core.checks.public_origin_unresolved (core.W002).
        from . import checks  # noqa: F401
