"""Tests for the Campañas screen: it mounts the same account nav and panels
as the CRM (core.crm), so these tests pin the sharing down -- same sections,
same views, same crm_panel endpoint, different mount."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.crm import ALL_VIEWS
from core.models import Client
from core.views import CLIENTS_PER_PAGE

HTMX = {"HX-Request": "true"}


class CampanasScreenTests(TestCase):
    def test_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["campanas"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="campanas-panel"')

    def test_mounts_the_shared_account_nav(self):
        response = self.client.get(reverse("section", args=["campanas"]))
        for text in ("Mi cuenta", "Gestión de clientes", "Calendario"):
            self.assertContains(response, text)

    def test_every_crm_view_renders_under_campanas(self):
        # As a master -- the Usuarios row is masters-only under either mount.
        self.client.force_login(get_user_model().objects.create_superuser("jefa"))
        html = self.client.get(reverse("section", args=["campanas"])).content.decode()
        for view in ALL_VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_nav_sections_are_collapsible_like_the_crm(self):
        # Campañas mounts the account nav the same way the CRM does: every
        # section is a <details> dropdown, none renders as a flat static group.
        for slug in ("campanas", "crm"):
            with self.subTest(section=slug):
                html = self.client.get(
                    reverse("section", args=[slug])
                ).content.decode()
                self.assertIn("<details", html)
                self.assertNotIn("side-nav__row--static", html)

    def test_clientes_is_the_default_view_here_too(self):
        response = self.client.get(reverse("section", args=["campanas"]))
        self.assertEqual(response.context["active_view"], "clientes")
        self.assertEqual(
            response.context["panel_template"], "partials/crm/panels/clientes.html"
        )

    def test_nav_rows_share_the_crm_panel_endpoint(self):
        # Fragments come from /crm/panel/... even under Campañas; only the
        # pushed URL and the swap target belong to this mount.
        html = self.client.get(reverse("section", args=["campanas"])).content.decode()
        self.assertIn('hx-get="/crm/panel/lista-clientes/"', html)
        self.assertIn('hx-target="#campanas-panel"', html)
        self.assertIn('hx-push-url="/s/campanas/?view=lista-clientes"', html)
        self.assertNotIn('hx-target="#crm-panel"', html)

    def test_lista_de_clientes_renders_identically_from_both_mounts(self):
        fragment = self.client.get(
            reverse("crm_panel", args=["lista-clientes"])
        ).content.decode()
        crm_page = self.client.get(
            reverse("section", args=["crm"]), {"view": "lista-clientes"}
        ).content.decode()
        campanas_page = self.client.get(
            reverse("section", args=["campanas"]), {"view": "lista-clientes"}
        ).content.decode()
        self.assertIn(fragment.strip(), crm_page)
        self.assertIn(fragment.strip(), campanas_page)

    def test_view_query_param_selects_the_panel(self):
        response = self.client.get(
            reverse("section", args=["campanas"]), {"view": "lista-clientes"}
        )
        self.assertEqual(response.context["active_view"], "lista-clientes")
        self.assertContains(response, "Sin lista de clientes")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["campanas"]), {"view": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "clientes")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["campanas"]), {"view": "etiquetas"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row--child is-active"), 1)

    def test_paging_the_client_table_stays_under_campanas(self):
        # The pager's push-url is relative, so paging from this mount must
        # never rewrite the address bar to the CRM section.
        Client.objects.bulk_create(
            Client(first_name=f"C{n:03d}", phone=f"+57{n}", country="CO")
            for n in range(CLIENTS_PER_PAGE + 5)
        )
        html = self.client.get(
            reverse("section", args=["campanas"]), {"view": "clientes"}
        ).content.decode()
        self.assertIn('hx-push-url="?view=clientes&page=2"', html)
        self.assertNotIn('hx-push-url="/s/crm/', html)

    def test_section_returns_a_bare_fragment_to_htmx(self):
        body = self.client.get(
            reverse("section", args=["campanas"]), headers=HTMX
        ).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("campanas-panel", body)
