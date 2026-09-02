"""Tests for the Estadísticas screen: flat secondary nav (with the Alpha
badge), the Mensajería stat-card grid, and the placeholder wiring."""

from django.test import TestCase
from django.urls import reverse

from core.estadisticas import CARDS, VIEWS

HTMX = {"HX-Request": "true"}


class EstadisticasScreenTests(TestCase):
    def test_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["estadisticas"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="estadisticas-panel"')

    def test_heading_is_menu_not_mi_cuenta(self):
        # This section's panel is headed "Menú", unlike the account navs.
        response = self.client.get(reverse("section", args=["estadisticas"]))
        self.assertContains(response, "Menú")
        html = response.content.decode()
        nav = html.split('id="estadisticas-panel"')[0].split("side-nav", 1)[1]
        self.assertNotIn("Mi cuenta", nav)

    def test_every_nav_row_renders(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        self.assertEqual(len(VIEWS), 7)
        for view in VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_nav_is_flat_with_no_collapsible_sections(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        self.assertNotIn("<details", html)

    def test_alpha_badge_renders_exactly_once(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        self.assertEqual(html.count('class="nav-badge"'), 1)
        row = html.split("?view=atribuciones", 1)[1].split("</a>", 1)[0]
        self.assertIn(">Alpha</span>", row)

    def test_mensajeria_is_the_default_view(self):
        response = self.client.get(reverse("section", args=["estadisticas"]))
        self.assertEqual(response.context["active_view"], "mensajeria")
        self.assertEqual(
            response.context["panel_template"],
            "partials/estadisticas/panels/mensajeria.html",
        )

    def test_view_query_param_selects_the_panel(self):
        # "atribuciones" because it is still on the placeholder; ventas,
        # etiquetas, embudos and temas-conversacion have real panels now
        # (their own test files).
        response = self.client.get(
            reverse("section", args=["estadisticas"]), {"view": "atribuciones"}
        )
        self.assertEqual(response.context["active_view"], "atribuciones")
        self.assertContains(response, "Atribuciones — próximamente")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["estadisticas"]), {"view": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "mensajeria")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["estadisticas"]), {"view": "embudos"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row is-active"), 1)
        active = html.split('?view=embudos"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_section_returns_a_bare_fragment_to_htmx(self):
        body = self.client.get(
            reverse("section", args=["estadisticas"]), headers=HTMX
        ).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("estadisticas-panel", body)


class MensajeriaCardsTests(TestCase):
    def test_header_and_subtitle_render(self):
        response = self.client.get(reverse("section", args=["estadisticas"]))
        self.assertContains(response, "Estadísticas de mensajería")
        self.assertContains(
            response, "Datos interesantes que tienes en cada plataforma de mensajería"
        )
        self.assertContains(response, "info-dot")

    def test_all_four_cards_render_with_title_and_text(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        self.assertEqual(len(CARDS), 4)
        self.assertEqual(html.count('class="stat-card"'), 4)
        for card in CARDS:
            with self.subTest(card.key):
                self.assertIn(card.title, html)
                self.assertIn(card.text, html)
                self.assertIn(card.icon, html)

    def test_whole_card_is_the_link(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        # The card element itself is an <a> wired to the detail route.
        self.assertIn('<a class="stat-card"', html)

    def test_cards_point_at_real_routes(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        for card in CARDS:
            with self.subTest(card.key):
                url = reverse("estadisticas_card", args=[card.key])
                self.assertIn(f'href="{url}"', html)
                self.assertIn(f'hx-get="{url}"', html)
        # Pin the target on each card's own tag -- the nav rows carry the same
        # attribute, so a page-wide assertIn would pass without the cards.
        card_tags = html.split('<a class="stat-card"')[1:]
        self.assertEqual(len(card_tags), 4)
        for tag in card_tags:
            self.assertIn('hx-target="#estadisticas-panel"', tag.split(">", 1)[0])

    def test_card_route_returns_a_placeholder_panel(self):
        # "volumen-mensajes" and "tiempos-respuesta" have real screens now
        # (tests_estadisticas_volumen / tests_estadisticas_tiempos); the other
        # two still resolve to the placeholder.
        response = self.client.get(
            reverse("estadisticas_card", args=["rendimiento-agentes"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rendimiento de Agentes — próximamente")

    def test_unknown_card_is_404(self):
        self.assertEqual(
            self.client.get("/estadisticas/mensajeria/bogus/").status_code, 404
        )


class EstadisticasPanelEndpointTests(TestCase):
    def test_returns_only_the_panel_fragment(self):
        response = self.client.get(reverse("estadisticas_panel", args=["ventas"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)  # nav panel is not re-sent
        self.assertIn("Estadísticas de ventas", body)

    def test_every_view_has_a_working_endpoint(self):
        for view in VIEWS:
            with self.subTest(view.key):
                response = self.client.get(
                    reverse("estadisticas_panel", args=[view.key])
                )
                self.assertEqual(response.status_code, 200)

    def test_unknown_view_is_404(self):
        self.assertEqual(
            self.client.get("/estadisticas/panel/bogus/").status_code, 404
        )

    def test_nav_rows_target_the_panel(self):
        html = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        self.assertIn('hx-target="#estadisticas-panel"', html)
        self.assertIn('hx-get="/estadisticas/panel/ventas/"', html)
        self.assertIn('hx-push-url="/s/estadisticas/?view=ventas"', html)

    def test_panel_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(
            reverse("estadisticas_panel", args=["mensajeria"])
        ).content.decode()
        page = self.client.get(reverse("section", args=["estadisticas"])).content.decode()
        self.assertIn(fragment.strip(), page)
