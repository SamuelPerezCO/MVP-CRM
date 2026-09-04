"""System checks for the app's own configuration.

Run by ``manage.py check``, and by ``runserver`` on every start, so an
environment that still holds a raw password says so out loud rather than
waiting to be noticed.
"""

from django.core.checks import Warning, register


@register()
def plaintext_env_secrets(app_configs, **kwargs):
    """Warn for every ``APP_AGENTS`` entry still carrying a raw password.

    Reading settings only -- no database -- so this is safe to run before
    migrations, during collectstatic, and in CI.
    """
    from . import agents

    plain = [agent.username for agent in agents.configured_agents() if not agent.is_hashed]
    if not plain:
        return []

    names = ", ".join(repr(name) for name in plain)
    return [
        Warning(
            f"Configured with a plaintext password: {names}.",
            hint=(
                "Anyone who can read the environment (the hosting dashboard, a "
                "CI log, a shared .env) can log in as them. Replace the middle "
                "field of each APP_AGENTS entry with a hash from "
                "`manage.py hashear_clave <usuario>`; login accepts either, so "
                "the swap needs no other change."
            ),
            id="core.W001",
        )
    ]
