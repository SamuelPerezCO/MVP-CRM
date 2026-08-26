"""Tests for the Mi comercio screen: the multi-section collapsible nav, the
Productos table with its status tabs, and the placeholder wiring."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.comercio import ALL_VIEWS, SECTIONS, STANDALONE, TABLE_COLUMNS, TABS
from core.models import Product

HTMX = {"HX-Request": "true"}


def make_product(name: str, status: str) -> Product:
    """One product row; tests create their own data, there are no seeds."""
    return Product.objects.create(
        name=name, stock=3, price=Decimal("9.99"), status=status
    )


class ComercioScreenTests(TestCase):
    def test_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["mi-comercio"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="comercio-panel"')

    def test_heading_renders(self):
        self.assertContains(
            self.client.get(reverse("section", args=["mi-comercio"])), "Mi cuenta"
        )

    def test_four_collapsible_sections_all_open(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        self.assertEqual(len(SECTIONS), 4)
        self.assertEqual(html.count('<details class="side-nav__section" open>'), 4)
        for section in SECTIONS:
            with self.subTest(section.key):
                self.assertIn(section.title, html)

    def test_every_nav_row_renders(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        self.assertEqual(len(ALL_VIEWS), 12)
        for view in ALL_VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_standalone_row_truncates_via_css_not_hardcoding(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        # Full label in the markup (title tooltip + visible text); the cut-off
        # is CSS-only, so the truncated string never appears server-side.
        self.assertEqual(html.count("Configuración del comercio"), 2)
        self.assertIn('title="Configuración del comercio"', html)
        self.assertIn("side-nav__row--single", html)
        self.assertNotIn("Configuración del come...", html)

    def test_standalone_row_takes_the_active_state(self):
        html = self.client.get(
            reverse("section", args=["mi-comercio"]), {"view": "configuracion-comercio"}
        ).content.decode()
        self.assertIn("side-nav__row--single is-active", html)
        self.assertNotIn("side-nav__row--child is-active", html)

    def test_productos_is_the_default_view(self):
        response = self.client.get(reverse("section", args=["mi-comercio"]))
        self.assertEqual(response.context["active_view"], "productos")
        self.assertEqual(
            response.context["panel_template"],
            "partials/comercio/panels/productos.html",
        )

    def test_view_query_param_selects_the_panel(self):
        response = self.client.get(
            reverse("section", args=["mi-comercio"]), {"view": "inventario"}
        )
        self.assertEqual(response.context["active_view"], "inventario")
        self.assertContains(response, "Inventario — próximamente")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["mi-comercio"]), {"view": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "productos")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["mi-comercio"]), {"view": "marcas"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row--child is-active"), 1)
        active = html.split('?view=marcas"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_section_returns_a_bare_fragment_to_htmx(self):
        body = self.client.get(
            reverse("section", args=["mi-comercio"]), headers=HTMX
        ).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("comercio-panel", body)


class ProductosPanelTests(TestCase):
    def test_header_and_toolbar_render(self):
        response = self.client.get(reverse("section", args=["mi-comercio"]))
        self.assertContains(response, "Productos")
        self.assertContains(response, "Buscar productos...")
        self.assertContains(response, "Importar")
        self.assertContains(response, "Crear +")

    def test_all_columns_render_with_info_dots(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        for column in TABLE_COLUMNS:
            with self.subTest(column):
                self.assertIn(column, html)
        # One info-dot per column; the Importar button's sits outside this region.
        table = html.split('id="product-table"', 1)[1]
        self.assertEqual(table.count('class="info-dot'), len(TABLE_COLUMNS))

    def test_select_all_checkbox_renders(self):
        self.assertContains(
            self.client.get(reverse("section", args=["mi-comercio"])),
            "Seleccionar todos los productos",
        )

    def test_empty_state_is_a_bare_header_no_message(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        tbody = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertNotIn("<tr", tbody)  # header renders, body stays blank

    def test_buttons_point_at_real_routes(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        for name in ["producto_create", "producto_import"]:
            with self.subTest(name):
                self.assertIn(f'href="{reverse(name)}"', html)
                self.assertIn(f'hx-get="{reverse(name)}"', html)
        self.assertIn('hx-target="#comercio-panel"', html)

    def test_button_routes_return_placeholder_panels(self):
        self.assertContains(
            self.client.get(reverse("producto_create")), "Crear producto — próximamente"
        )
        self.assertContains(
            self.client.get(reverse("producto_import")),
            "Importar productos — próximamente",
        )


class ProductosTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.active = make_product("Camiseta", "activo")
        cls.inactive = make_product("Gorra", "inactivo")

    def test_activos_is_the_default_tab(self):
        response = self.client.get(reverse("section", args=["mi-comercio"]))
        self.assertEqual(response.context["active_tab"], "activos")

    def test_default_tab_filters_to_active_products(self):
        response = self.client.get(reverse("section", args=["mi-comercio"]))
        self.assertContains(response, "Camiseta")
        self.assertNotContains(response, "Gorra")

    def test_todos_shows_everything(self):
        response = self.client.get(
            reverse("section", args=["mi-comercio"]), {"tab": "todos"}
        )
        self.assertContains(response, "Camiseta")
        self.assertContains(response, "Gorra")

    def test_inactivos_shows_only_inactive(self):
        response = self.client.get(
            reverse("section", args=["mi-comercio"]), {"tab": "inactivos"}
        )
        self.assertNotContains(response, "Camiseta")
        self.assertContains(response, "Gorra")

    def test_unknown_tab_falls_back_instead_of_404(self):
        response = self.client.get(
            reverse("section", args=["mi-comercio"]), {"tab": "bogus"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "activos")

    def test_exactly_one_tab_is_active(self):
        html = self.client.get(
            reverse("section", args=["mi-comercio"]), {"tab": "inactivos"}
        ).content.decode()
        self.assertEqual(html.count("product-tabs__tab is-active"), 1)
        active = html.split('?view=productos&tab=inactivos"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_row_renders_every_column_value(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        row = html.split("Camiseta", 1)[1].split("</tr>", 1)[0]
        self.assertIn("<td>3</td>", row)  # the stock cell, exactly
        self.assertIn("9.99", row)       # price
        self.assertIn("Activo", row)     # estado display


class ProductosTableEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_product("Camiseta", "activo")

    def test_returns_only_the_table_region(self):
        response = self.client.get(reverse("productos_table", args=["todos"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)       # nav panel is not re-sent
        self.assertNotIn("productos__toolbar", body)  # toolbar stays put
        self.assertIn("product-tabs", body)      # tabs travel with the region
        self.assertIn("Camiseta", body)

    def test_every_tab_has_a_working_endpoint(self):
        for tab in TABS:
            with self.subTest(tab.key):
                response = self.client.get(reverse("productos_table", args=[tab.key]))
                self.assertEqual(response.status_code, 200)

    def test_unknown_tab_is_404(self):
        self.assertEqual(
            self.client.get("/comercio/productos/tab/bogus/").status_code, 404
        )

    def test_tab_slugs_do_not_swallow_the_button_routes(self):
        # nuevo/importar live under productos/ too; the tab/ prefix keeps them apart.
        self.assertEqual(self.client.get("/comercio/productos/nuevo/").status_code, 200)
        self.assertEqual(
            self.client.get("/comercio/productos/importar/").status_code, 200
        )

    def test_tabs_target_the_table_region(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        self.assertIn('hx-target="#product-table"', html)
        self.assertIn('hx-get="/comercio/productos/tab/todos/"', html)
        self.assertIn('hx-push-url="/s/mi-comercio/?view=productos&tab=todos"', html)

    def test_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(
            reverse("productos_table", args=["activos"])
        ).content.decode()
        page = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        self.assertIn(fragment.strip(), page)


class ComercioPanelEndpointTests(TestCase):
    def test_returns_only_the_panel_fragment(self):
        response = self.client.get(reverse("comercio_panel", args=["inventario"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)  # nav panel is not re-sent
        self.assertIn("Inventario", body)

    def test_every_view_has_a_working_endpoint(self):
        for view in ALL_VIEWS:
            with self.subTest(view.key):
                response = self.client.get(reverse("comercio_panel", args=[view.key]))
                self.assertEqual(response.status_code, 200)

    def test_standalone_view_resolves_too(self):
        response = self.client.get(reverse("comercio_panel", args=[STANDALONE.key]))
        self.assertContains(response, "Configuración del comercio — próximamente")

    def test_unknown_view_is_404(self):
        self.assertEqual(self.client.get("/comercio/panel/bogus/").status_code, 404)

    def test_nav_rows_target_the_panel(self):
        html = self.client.get(reverse("section", args=["mi-comercio"])).content.decode()
        self.assertIn('hx-target="#comercio-panel"', html)
        self.assertIn('hx-get="/comercio/panel/inventario/"', html)
        self.assertIn('hx-push-url="/s/mi-comercio/?view=inventario"', html)
