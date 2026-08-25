"""Tests for the Embudos screen: flat secondary nav and the empty-state card."""

from django.test import TestCase
from django.urls import reverse

from core.embudos import VIEWS

HTMX = {"HX-Request": "true"}


class EmbudosScreenTests(TestCase):
    def test_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["embudos"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="embudos-panel"')

    def test_heading_renders(self):
        self.assertContains(self.client.get(reverse("section", args=["embudos"])), "Mi cuenta")

    def test_every_nav_row_renders_with_its_icon(self):
        html = self.client.get(reverse("section", args=["embudos"])).content.decode()
        self.assertEqual(len(VIEWS), 3)
        for view in VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_nav_is_flat_with_no_collapsible_sections(self):
        html = self.client.get(reverse("section", args=["embudos"])).content.decode()
        self.assertNotIn("<details", html)

    def test_embudos_is_the_default_view(self):
        response = self.client.get(reverse("section", args=["embudos"]))
        self.assertEqual(response.context["active_view"], "embudos")
        self.assertEqual(
            response.context["panel_template"], "partials/embudos/panels/embudos.html"
        )

    def test_view_query_param_selects_the_panel(self):
        response = self.client.get(
            reverse("section", args=["embudos"]), {"view": "historial-descargas"}
        )
        self.assertEqual(response.context["active_view"], "historial-descargas")
        self.assertContains(response, "Historial de descargas — próximamente")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(reverse("section", args=["embudos"]), {"view": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "embudos")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["embudos"]), {"view": "automatizaciones"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row is-active"), 1)
        active = html.split('?view=automatizaciones"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)


class EmbudosEmptyStateTests(TestCase):
    def test_card_copy_renders(self):
        response = self.client.get(reverse("section", args=["embudos"]))
        self.assertContains(response, "Organiza tu proceso con embudos de venta")
        self.assertContains(response, "Los embudos en Mercately te permiten seguir el recorrido")
        self.assertContains(response, "Comienza creando tu primer embudo")
        self.assertContains(response, "Crear nuevo embudo")

    def test_no_illustration_is_rendered(self):
        # The reference's isometric graphic is deliberately omitted.
        html = self.client.get(reverse("section", args=["embudos"])).content.decode()
        panel = html.split('id="embudos-panel"', 1)[1]
        self.assertNotIn("<img", panel)
        self.assertNotIn("<svg", panel)

    def test_empty_state_shows_only_while_there_are_no_funnels(self):
        response = self.client.get(reverse("section", args=["embudos"]))
        self.assertEqual(response.context["funnels"], [])
        self.assertContains(response, "card--intro")
        self.assertNotContains(response, "funnel-list")

    def test_create_button_points_at_a_real_route(self):
        html = self.client.get(reverse("section", args=["embudos"])).content.decode()
        self.assertIn(f'href="{reverse("embudo_create")}"', html)
        self.assertIn(f'hx-get="{reverse("embudo_create")}"', html)
        self.assertIn('hx-target="#embudos-panel"', html)

    def test_create_route_returns_a_placeholder_panel(self):
        response = self.client.get(reverse("embudo_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crear nuevo embudo — próximamente")


class EmbudosPanelEndpointTests(TestCase):
    def test_returns_only_the_panel_fragment(self):
        response = self.client.get(reverse("embudos_panel", args=["automatizaciones"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)  # nav panel is not re-sent
        self.assertIn("Automatizaciones", body)

    def test_every_view_has_a_working_endpoint(self):
        for view in VIEWS:
            with self.subTest(view.key):
                response = self.client.get(reverse("embudos_panel", args=[view.key]))
                self.assertEqual(response.status_code, 200)

    def test_unknown_view_is_404(self):
        self.assertEqual(self.client.get("/embudos/panel/bogus/").status_code, 404)

    def test_nav_rows_target_the_panel(self):
        html = self.client.get(reverse("section", args=["embudos"])).content.decode()
        self.assertIn('hx-target="#embudos-panel"', html)
        self.assertIn('hx-get="/embudos/panel/automatizaciones/"', html)
        self.assertIn('hx-push-url="/s/embudos/?view=automatizaciones"', html)

    def test_panel_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(reverse("embudos_panel", args=["embudos"])).content.decode()
        page = self.client.get(reverse("section", args=["embudos"])).content.decode()
        self.assertIn(fragment.strip(), page)

    def test_section_returns_a_bare_fragment_to_htmx(self):
        body = self.client.get(
            reverse("section", args=["embudos"]), headers=HTMX
        ).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("embudos-panel", body)
