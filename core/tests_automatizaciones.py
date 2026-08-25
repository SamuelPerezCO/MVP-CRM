"""Tests for the Automatizaciones screen: secondary nav (with the expandable
MIA row), the Academy banner and the minimal Flujos empty state."""

from django.test import TestCase
from django.urls import reverse

from core.automatizaciones import VIEWS

HTMX = {"HX-Request": "true"}


class AutomatizacionesScreenTests(TestCase):
    def test_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["automatizaciones"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="autom-panel"')

    def test_heading_renders(self):
        self.assertContains(
            self.client.get(reverse("section", args=["automatizaciones"])), "Mi cuenta"
        )

    def test_every_nav_row_renders(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        self.assertEqual(len(VIEWS), 4)
        for view in VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_mia_row_is_the_only_expandable_one(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        self.assertEqual(html.count("<details"), 1)
        self.assertIn("side-nav__row--summary", html)
        self.assertIn("side-nav__children", html)
        # Its child slot is deliberately empty until the sub-items are defined.
        self.assertNotIn("side-nav__row--child", html)

    def test_chatbots_de_flujo_is_the_default_view(self):
        response = self.client.get(reverse("section", args=["automatizaciones"]))
        self.assertEqual(response.context["active_view"], "chatbots-flujo")
        self.assertEqual(
            response.context["panel_template"],
            "partials/automatizaciones/panels/chatbots-flujo.html",
        )

    def test_view_query_param_selects_the_panel(self):
        response = self.client.get(
            reverse("section", args=["automatizaciones"]), {"view": "mensajes-programados"}
        )
        self.assertEqual(response.context["active_view"], "mensajes-programados")
        self.assertContains(response, "Mensajes programados — próximamente")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["automatizaciones"]), {"view": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "chatbots-flujo")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["automatizaciones"]), {"view": "flujos-whatsapp"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row is-active"), 1)
        active = html.split('?view=flujos-whatsapp"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)


class AcademyBannerTests(TestCase):
    def test_banner_copy_renders(self):
        response = self.client.get(reverse("section", args=["automatizaciones"]))
        self.assertContains(response, "Academy:")
        self.assertContains(response, "Aprende qué son los WhatsApp Flows")
        self.assertContains(response, "Completado")

    def test_banner_is_dismissible(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        # The hooks shell.js keys off: the storage key and the close button.
        self.assertIn('data-dismissible="academy-flujos"', html)
        self.assertIn("data-dismiss", html)

    def test_progress_bar_renders_at_zero(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        self.assertIn('role="progressbar"', html)
        self.assertIn('aria-valuenow="0"', html)


class FlujosEmptyStateTests(TestCase):
    def test_header_row_renders(self):
        response = self.client.get(reverse("section", args=["automatizaciones"]))
        self.assertContains(response, "Flujos")
        self.assertContains(response, "info-dot")
        self.assertContains(response, "+ Añadir flujo")

    def test_empty_state_is_minimal_text_only(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        empty = html.split('class="flows-empty"', 1)[1].split("</div>", 1)[0]
        self.assertIn("Sin flujos", empty)
        self.assertNotIn("<svg", empty)  # unlike the Inbox/Embudos empty states
        self.assertNotIn("<img", empty)

    def test_empty_state_shows_only_while_there_are_no_flows(self):
        response = self.client.get(reverse("section", args=["automatizaciones"]))
        self.assertEqual(response.context["flows"], [])
        self.assertContains(response, "flows-empty")
        self.assertNotContains(response, "flow-list")

    def test_add_button_points_at_a_real_route(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        self.assertIn(f'href="{reverse("flujo_create")}"', html)
        self.assertIn(f'hx-get="{reverse("flujo_create")}"', html)
        self.assertIn('hx-target="#autom-panel"', html)

    def test_add_route_returns_a_placeholder_panel(self):
        response = self.client.get(reverse("flujo_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Añadir flujo — próximamente")


class AutomatizacionesPanelEndpointTests(TestCase):
    def test_returns_only_the_panel_fragment(self):
        response = self.client.get(
            reverse("automatizaciones_panel", args=["mensajes-programados"])
        )
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)  # nav panel is not re-sent
        self.assertIn("Mensajes programados", body)

    def test_every_view_has_a_working_endpoint(self):
        for view in VIEWS:
            with self.subTest(view.key):
                response = self.client.get(
                    reverse("automatizaciones_panel", args=[view.key])
                )
                self.assertEqual(response.status_code, 200)

    def test_unknown_view_is_404(self):
        self.assertEqual(
            self.client.get("/automatizaciones/panel/bogus/").status_code, 404
        )

    def test_nav_rows_target_the_panel(self):
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        self.assertIn('hx-target="#autom-panel"', html)
        self.assertIn('hx-get="/automatizaciones/panel/mensajes-programados/"', html)
        self.assertIn('hx-push-url="/s/automatizaciones/?view=mensajes-programados"', html)

    def test_expandable_summary_also_swaps_the_panel(self):
        # MIA is a <summary>, not an <a>, but still a destination: it carries
        # the same hx-* wiring so clicking it toggles *and* loads its panel.
        html = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        summary = html.split("<summary", 1)[1].split(">", 1)[0]
        self.assertIn('hx-get="/automatizaciones/panel/mia-agentes/"', summary)
        self.assertIn("data-nav-item", summary)

    def test_panel_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(
            reverse("automatizaciones_panel", args=["chatbots-flujo"])
        ).content.decode()
        page = self.client.get(reverse("section", args=["automatizaciones"])).content.decode()
        self.assertIn(fragment.strip(), page)

    def test_section_returns_a_bare_fragment_to_htmx(self):
        body = self.client.get(
            reverse("section", args=["automatizaciones"]), headers=HTMX
        ).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("autom-panel", body)
