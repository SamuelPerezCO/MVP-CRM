"""Tests for the Configuración de mensajería screen: headless flat nav, the
Plantillas de WhatsApp tabbed table, and the two-half empty state."""

from django.test import TestCase
from django.urls import reverse

from core.mensajeria import TABLE_COLUMNS, TABS, VIEWS
from core.models import MessageTemplate

HTMX = {"HX-Request": "true"}


def make_template(name: str, status: str = "pendiente", active: bool = True):
    """One template row; tests create their own data, there are no seeds."""
    return MessageTemplate.objects.create(
        name=name, template_type="Texto", category="Marketing",
        text=f"Hola, {name}", team="Ventas", status=status, is_active=active,
    )


class MensajeriaScreenTests(TestCase):
    def test_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["mensajeria"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="mensajeria-panel"')

    def test_nav_has_no_heading(self):
        # Unlike the other panels, this list starts at the top of the column.
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertNotIn("side-nav__heading", html)
        self.assertIn("side-nav__scroll--headless", html)

    def test_every_nav_row_renders(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertEqual(len(VIEWS), 7)
        for view in VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_plantillas_is_the_default_view(self):
        response = self.client.get(reverse("section", args=["mensajeria"]))
        self.assertEqual(response.context["active_view"], "plantillas-whatsapp")
        self.assertEqual(
            response.context["panel_template"],
            "partials/mensajeria/panels/plantillas-whatsapp.html",
        )

    def test_view_query_param_selects_the_panel(self):
        response = self.client.get(
            reverse("section", args=["mensajeria"]), {"view": "respuestas-rapidas"}
        )
        self.assertEqual(response.context["active_view"], "respuestas-rapidas")
        self.assertContains(response, "Respuestas rápidas — próximamente")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["mensajeria"]), {"view": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "plantillas-whatsapp")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["mensajeria"]), {"view": "reglas-mensajeria"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row is-active"), 1)
        active = html.split('?view=reglas-mensajeria"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_section_returns_a_bare_fragment_to_htmx(self):
        body = self.client.get(
            reverse("section", args=["mensajeria"]), headers=HTMX
        ).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("mensajeria-panel", body)


class PlantillasPanelTests(TestCase):
    def test_toolbar_renders_without_a_page_title(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertIn("Buscar por nombre", html)
        self.assertIn("Crear plantilla +", html)
        # No heading in the panel, per the reference: the toolbar sits alone.
        panel = html.split('id="mensajeria-panel"', 1)[1]
        self.assertNotIn("<h1", panel.split('id="template-table"')[0])

    def test_all_columns_render(self):
        response = self.client.get(reverse("section", args=["mensajeria"]))
        for column in TABLE_COLUMNS:
            self.assertContains(response, column)

    def test_no_checkbox_column(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertNotIn('type="checkbox"', html)

    def test_empty_state_renders_both_halves(self):
        response = self.client.get(reverse("section", args=["mensajeria"]))
        self.assertContains(
            response, "Crea plantillas de WhatsApp listas para automatizar tus mensajes"
        )
        self.assertContains(response, "Usa plantillas para iniciar conversaciones")
        self.assertContains(response, "Crear nueva plantilla")
        self.assertContains(response, "tpl-art")  # the placeholder illustration

    def test_both_create_buttons_share_the_same_route(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        url = reverse("plantilla_create")
        self.assertEqual(html.count(f'hx-get="{url}"'), 2)   # toolbar + empty state
        self.assertEqual(html.count(f'href="{url}"'), 2)     # non-JS fallback on both
        # Scoped to the panel: the nav rows carry the same hx-target.
        panel = html.split('id="mensajeria-panel"', 1)[1]
        self.assertEqual(panel.count('hx-target="#mensajeria-panel"'), 2)

    def test_create_route_returns_a_placeholder_panel(self):
        response = self.client.get(reverse("plantilla_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crear plantilla — próximamente")

    def test_empty_state_hidden_once_templates_exist(self):
        make_template("Bienvenida")
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertIn("Bienvenida", html)
        self.assertNotIn("tpl-empty", html)
        self.assertNotIn("tpl-art", html)


class PlantillasTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_template("Pendiente A", status="pendiente")
        make_template("Aceptada B", status="aceptada")
        make_template("Rechazada C", status="rechazada")
        make_template("Apagada D", status="aceptada", active=False)

    def test_todas_is_the_default_tab_and_shows_everything(self):
        response = self.client.get(reverse("section", args=["mensajeria"]))
        self.assertEqual(response.context["active_tab"], "todas")
        for name in ("Pendiente A", "Aceptada B", "Rechazada C", "Apagada D"):
            self.assertContains(response, name)

    def test_status_tabs_filter_by_status(self):
        response = self.client.get(
            reverse("section", args=["mensajeria"]), {"tab": "aceptadas"}
        )
        self.assertContains(response, "Aceptada B")
        self.assertContains(response, "Apagada D")  # aceptada, though switched off
        self.assertNotContains(response, "Pendiente A")
        self.assertNotContains(response, "Rechazada C")

    def test_desactivadas_filters_the_toggle_not_the_status(self):
        response = self.client.get(
            reverse("section", args=["mensajeria"]), {"tab": "desactivadas"}
        )
        self.assertContains(response, "Apagada D")
        self.assertNotContains(response, "Aceptada B")

    def test_unknown_tab_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["mensajeria"]), {"tab": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "todas")

    def test_exactly_one_tab_is_active(self):
        html = self.client.get(
            reverse("section", args=["mensajeria"]), {"tab": "rechazadas"}
        ).content.decode()
        self.assertEqual(html.count("product-tabs__tab is-active"), 1)
        active = html.split("&tab=rechazadas")[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_row_renders_every_column_value(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        row = html.split("Aceptada B", 1)[1].split("</tr>", 1)[0]
        self.assertIn("Texto", row)         # tipo
        self.assertIn("Marketing", row)     # categoría
        self.assertIn("Hola, Aceptada B", row)
        self.assertIn("Ventas", row)        # equipo
        self.assertIn("<td>Sí</td>", row)   # activo
        self.assertIn("Aceptada</td>", row)  # estado display

    def test_empty_tab_shows_empty_state_while_others_have_rows(self):
        MessageTemplate.objects.filter(status="rechazada").delete()
        response = self.client.get(
            reverse("section", args=["mensajeria"]), {"tab": "rechazadas"}
        )
        self.assertContains(response, "tpl-empty")


class PlantillasTableEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_template("Bienvenida")

    def test_returns_only_the_table_region(self):
        response = self.client.get(reverse("plantillas_table", args=["todas"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)          # nav panel is not re-sent
        self.assertNotIn("plantillas__toolbar", body)  # toolbar stays put
        self.assertIn("product-tabs", body)         # tabs travel with the region
        self.assertIn("Bienvenida", body)

    def test_every_tab_has_a_working_endpoint(self):
        for tab in TABS:
            with self.subTest(tab.key):
                response = self.client.get(reverse("plantillas_table", args=[tab.key]))
                self.assertEqual(response.status_code, 200)

    def test_unknown_tab_is_404(self):
        self.assertEqual(
            self.client.get("/mensajeria/plantillas/tab/bogus/").status_code, 404
        )

    def test_tab_slugs_do_not_swallow_the_create_route(self):
        self.assertEqual(
            self.client.get("/mensajeria/plantillas/nueva/").status_code, 200
        )

    def test_tabs_target_the_table_region(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertIn('hx-target="#template-table"', html)
        self.assertIn('hx-get="/mensajeria/plantillas/tab/aceptadas/"', html)
        self.assertIn(
            'hx-push-url="/s/mensajeria/?view=plantillas-whatsapp&tab=aceptadas"', html
        )

    def test_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(
            reverse("plantillas_table", args=["todas"])
        ).content.decode()
        page = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertIn(fragment.strip(), page)


class MensajeriaPanelEndpointTests(TestCase):
    def test_returns_only_the_panel_fragment(self):
        response = self.client.get(
            reverse("mensajeria_panel", args=["widget-whatsapp"])
        )
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)  # nav panel is not re-sent
        self.assertIn("Widget de WhatsApp", body)

    def test_every_view_has_a_working_endpoint(self):
        for view in VIEWS:
            with self.subTest(view.key):
                response = self.client.get(reverse("mensajeria_panel", args=[view.key]))
                self.assertEqual(response.status_code, 200)

    def test_unknown_view_is_404(self):
        self.assertEqual(self.client.get("/mensajeria/panel/bogus/").status_code, 404)

    def test_panel_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(
            reverse("mensajeria_panel", args=["plantillas-whatsapp"])
        ).content.decode()
        page = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertIn(fragment.strip(), page)

    def test_nav_rows_target_the_panel(self):
        html = self.client.get(reverse("section", args=["mensajeria"])).content.decode()
        self.assertIn('hx-target="#mensajeria-panel"', html)
        self.assertIn('hx-get="/mensajeria/panel/widget-whatsapp/"', html)
        self.assertIn('hx-push-url="/s/mensajeria/?view=widget-whatsapp"', html)
