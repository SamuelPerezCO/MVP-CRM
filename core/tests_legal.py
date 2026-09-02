"""The public legal pages -- privacy policy and data deletion.

Their whole point is being reachable *without* a session: Meta will not publish
an app whose privacy policy sits behind a login, and a customer asking us to
delete their data has no account to log into either.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from core.middleware import LoginRequiredMiddleware


class LegalPagesTests(TestCase):
    def test_privacy_policy_renders(self):
        response = self.client.get(reverse('privacy'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Política de privacidad')
        # The substance Meta's review looks for, not just a title.
        self.assertContains(response, 'Ley 1581 de 2012')

    def test_data_deletion_page_renders(self):
        response = self.client.get(reverse('data_deletion'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Eliminación de datos')

    def test_the_pages_are_exempt_from_the_login_gate(self):
        """The real assertion of this module.

        ``settings.TESTING`` disables the gate wholesale, so fetching these URLs
        with the test client would return 200 even if they were gated -- the
        exemption rule itself has to be checked directly.
        """
        middleware = LoginRequiredMiddleware(lambda request: None)

        for path in ('/privacidad/', '/eliminacion-de-datos/'):
            with self.subTest(path=path):
                self.assertTrue(middleware._is_exempt(path))

    def test_the_gate_still_covers_the_app_itself(self):
        """Guards against someone widening the exemption into a hole."""
        middleware = LoginRequiredMiddleware(lambda request: None)

        for path in ('/', '/inbox/list/todos/', '/s/crm/'):
            with self.subTest(path=path):
                self.assertFalse(middleware._is_exempt(path))

    @override_settings(
        LEGAL_ENTITY_NAME='Acme SAS', LEGAL_CONTACT_EMAIL='hola@acme.co'
    )
    def test_responsible_party_comes_from_settings(self):
        """The pages name a real business, set per deployment -- not a value
        hardcoded into a template nobody remembers to edit."""
        for url_name in ('privacy', 'data_deletion'):
            with self.subTest(page=url_name):
                response = self.client.get(reverse(url_name))
                self.assertContains(response, 'Acme SAS')
                self.assertContains(response, 'hola@acme.co')

    def test_the_pages_link_to_each_other(self):
        """Meta asks for both URLs; each should lead to the other."""
        privacy = self.client.get(reverse('privacy'))
        deletion = self.client.get(reverse('data_deletion'))

        self.assertContains(privacy, reverse('data_deletion'))
        self.assertContains(deletion, reverse('privacy'))
