from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        # Registers the system checks by import side effect (core/checks.py).
        from . import checks  # noqa: F401
