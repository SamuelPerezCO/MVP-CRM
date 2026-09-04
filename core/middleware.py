"""App-wide login gate.

Every request needs a session that logged in through core.views.login_view --
against the accounts in core.agents (the APP_AGENTS environment list and the
database users a master creates) -- except for the handful of paths that must
stay reachable without a browser session: provider webhooks (hit by
Twilio/Meta/the Baileys sidecar, authenticated by their own signature check
instead), the public legal pages, static/media assets, and Django admin
(gated separately by django.contrib.auth).

Two things have to hold. The session must carry the flag login_view sets, and
django.contrib.auth must still resolve it to a real user: AuthenticationMiddleware
hands back AnonymousUser for an account that was deleted, or deactivated
(ModelBackend.get_user checks is_active), since the session was created. So a
master deactivating someone locks them out on their very next request, flag or
no flag -- and the dead session is flushed so their next login starts clean.
"""

from django.shortcuts import redirect
from django.urls import reverse

from django.conf import settings

SESSION_KEY = 'app_authenticated'

# /privacidad/ and /eliminacion-de-datos/ are public on purpose: Meta's app
# review reads them without an account, and so must any customer.
EXEMPT_PATHS = {'/login/', '/privacidad/', '/eliminacion-de-datos/'}
EXEMPT_PREFIXES = ('/webhooks/', '/static/', '/media/', '/admin/')


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.TESTING or self._is_exempt(request.path):
            return self.get_response(request)
        if request.session.get(SESSION_KEY):
            if request.user.is_authenticated:
                return self.get_response(request)
            # The flag outlived the account behind it.
            request.session.flush()
        login_url = reverse('login')
        if request.path != '/':
            login_url = f'{login_url}?next={request.path}'
        return redirect(login_url)

    def _is_exempt(self, path):
        return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)
