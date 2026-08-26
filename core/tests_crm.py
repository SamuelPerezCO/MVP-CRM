"""Tests for the CRM screen: model helpers, secondary nav, and the client table."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.crm import ALL_VIEWS
from core.models import Client, ClientList
from core.views import CLIENTS_PER_PAGE

CO_FLAG = "\U0001F1E8\U0001F1F4"


class ClientModelTests(TestCase):
    def test_full_name_joins_and_trims(self):
        self.assertEqual(Client(first_name="Samuel", last_name="Perez").full_name, "Samuel Perez")
        self.assertEqual(Client(first_name="Samuel", last_name="").full_name, "Samuel")

    def test_flag_from_country_code(self):
        self.assertEqual(Client(country="CO").flag, CO_FLAG)
        self.assertEqual(Client(country="co").flag, CO_FLAG)

    def test_flag_is_blank_when_country_is_missing_or_malformed(self):
        for bad in ("", "C", "COL", "12"):
            with self.subTest(bad):
                self.assertEqual(Client(country=bad).flag, "")

    def test_has_whatsapp_only_for_the_whatsapp_channel(self):
        self.assertTrue(Client(channel="whatsapp").has_whatsapp)
        for other in ("messenger", "instagram", ""):
            with self.subTest(other):
                self.assertFalse(Client(channel=other).has_whatsapp)

    def test_whatsapp_url_strips_formatting(self):
        self.assertEqual(
            Client(phone="+57 316 768 7288").whatsapp_url, "https://wa.me/573167687288"
        )


class CrmScreenTests(TestCase):
    def test_crm_renders_both_columns(self):
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertContains(response, "side-nav")
        self.assertContains(response, 'id="crm-panel"')

    def test_heading_and_section_titles_render(self):
        response = self.client.get(reverse("section", args=["crm"]))
        for text in ("Mi cuenta", "Gestión de clientes", "Calendario"):
            self.assertContains(response, text)

    def test_every_nav_view_renders_as_a_link(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        for view in ALL_VIEWS:
            with self.subTest(view.key):
                self.assertIn(f"?view={view.key}", html)
                self.assertIn(view.label, html)

    def test_both_sections_start_expanded(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertEqual(html.count('<details class="side-nav__section" open>'), 2)

    def test_clientes_is_the_default_view(self):
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertEqual(response.context["active_view"], "clientes")
        self.assertEqual(
            response.context["panel_template"], "partials/crm/panels/clientes.html"
        )

    def test_view_query_param_selects_the_panel(self):
        response = self.client.get(reverse("section", args=["crm"]), {"view": "etiquetas"})
        self.assertEqual(response.context["active_view"], "etiquetas")
        self.assertContains(response, "Etiquetas — próximamente")

    def test_unknown_view_falls_back_instead_of_404(self):
        response = self.client.get(reverse("section", args=["crm"]), {"view": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_view"], "clientes")

    def test_exactly_one_row_is_active(self):
        html = self.client.get(
            reverse("section", args=["crm"]), {"view": "exportaciones"}
        ).content.decode()
        self.assertEqual(html.count("side-nav__row--child is-active"), 1)
        active = html.split('?view=exportaciones"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_toolbar_controls_render(self):
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertContains(response, "Buscar por nombre, teléfono o correo")
        self.assertContains(response, "Acciones")
        self.assertContains(response, "+ Crear cliente")

    def test_table_headers_render_once_there_are_rows(self):
        # With no clients the panel shows the empty state instead of table chrome.
        Client.objects.create(first_name="Ana", phone="+571", country="CO")
        response = self.client.get(reverse("section", args=["crm"]))
        for header in ("Nombres y apellidos", "Teléfono", "Mail", "Canal", "Acciones"):
            self.assertContains(response, header)


class ClientTableTests(TestCase):
    def test_empty_state_when_there_are_no_clients(self):
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertContains(response, "Aún no tienes clientes")
        self.assertNotContains(response, "<tbody>")

    def test_row_renders_name_phone_flag_mail_and_channel(self):
        Client.objects.create(
            first_name="Samuel",
            last_name="Perez",
            phone="+573167687288",
            country="CO",
            email="s@example.com",
            channel="whatsapp",
        )
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertContains(response, "Samuel Perez")
        self.assertContains(response, "+573167687288")
        self.assertContains(response, 'class="flag"')   # vendored CO svg, not the emoji
        self.assertContains(response, "s@example.com")
        self.assertContains(response, "WhatsApp")
        self.assertNotContains(response, "Aún no tienes clientes")

    def test_whatsapp_link_only_for_whatsapp_clients(self):
        Client.objects.create(first_name="Con", phone="+571", country="CO", channel="whatsapp")
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn("Iniciar conversación", html)

        Client.objects.all().delete()
        Client.objects.create(first_name="Sin", phone="+572", country="CO", channel="messenger")
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertNotIn("Iniciar conversación", html)

    def test_country_without_a_vendored_svg_falls_back_to_the_emoji(self):
        # No templates/icons/flags/fr.svg exists, so the emoji is used instead.
        Client.objects.create(first_name="Luc", phone="+331", country="FR")
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertNotContains(response, 'class="flag"')
        self.assertContains(response, "🇫🇷")

    def test_row_actions_are_labelled_per_client(self):
        Client.objects.create(first_name="Ana", last_name="Gil", phone="+573", country="CO")
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertContains(response, 'aria-label="Ver Ana Gil"')
        self.assertContains(response, 'aria-label="Editar Ana Gil"')

    def test_no_pager_on_a_single_page(self):
        Client.objects.create(first_name="Solo", phone="+574", country="CO")
        self.assertNotContains(
            self.client.get(reverse("section", args=["crm"])), "pager__status"
        )

    def test_pager_swaps_a_region_endpoint_not_the_full_panel(self):
        # The pager targets #client-table, so it must fetch only that region:
        # fetching the whole Clientes panel here used to nest a second toolbar
        # and a duplicate #client-table inside the first on every page click.
        Client.objects.bulk_create(
            Client(first_name=f"C{n:03d}", phone=f"+57{n}", country="CO")
            for n in range(CLIENTS_PER_PAGE + 5)
        )
        page = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn(f"hx-get=\"{reverse('clientes_table')}?page=2\"", page)

        response = self.client.get(reverse("clientes_table"), {"page": 2})
        region = response.content.decode()
        self.assertNotIn("crm-panel__toolbar", region)   # toolbar stays put
        self.assertNotIn('id="client-table"', region)    # no duplicate id
        self.assertEqual(len(response.context["clients"]), 5)

    def test_pager_appears_and_splits_the_queryset(self):
        Client.objects.bulk_create(
            Client(first_name=f"C{n:03d}", phone=f"+57{n}", country="CO")
            for n in range(CLIENTS_PER_PAGE + 5)
        )
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertContains(response, "pager__status")
        self.assertEqual(len(response.context["clients"]), CLIENTS_PER_PAGE)

        page2 = self.client.get(reverse("section", args=["crm"]), {"page": 2})
        self.assertEqual(len(page2.context["clients"]), 5)


class CrmPanelEndpointTests(TestCase):
    def test_returns_only_the_panel_fragment(self):
        response = self.client.get(reverse("crm_panel", args=["clientes"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)  # nav panel is not re-sent
        self.assertIn("Clientes", body)

    def test_every_view_has_a_working_endpoint(self):
        for view in ALL_VIEWS:
            with self.subTest(view.key):
                response = self.client.get(reverse("crm_panel", args=[view.key]))
                self.assertEqual(response.status_code, 200)

    def test_unknown_view_is_404(self):
        self.assertEqual(self.client.get("/crm/panel/bogus/").status_code, 404)

    def test_nav_rows_target_the_panel(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn('hx-target="#crm-panel"', html)
        self.assertIn('hx-get="/crm/panel/etiquetas/"', html)
        self.assertIn('hx-push-url="/s/crm/?view=etiquetas"', html)

    def test_panel_endpoint_matches_what_the_page_embeds(self):
        fragment = self.client.get(reverse("crm_panel", args=["clientes"])).content.decode()
        page = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn(fragment.strip(), page)


class ClientListTests(TestCase):
    """The Lista de clientes panel: toolbar, table and its minimal empty state."""

    URL = reverse("section", args=["crm"]) + "?view=lista-clientes"

    def test_header_and_toolbar_render(self):
        response = self.client.get(self.URL)
        self.assertContains(response, "Lista de clientes")
        self.assertContains(response, "Acciones")
        self.assertContains(response, "+ Crear lista")
        # Two info dots: beside the title and in the toolbar.
        panel = response.content.decode().split('id="crm-panel"', 1)[1]
        self.assertEqual(panel.count('class="info-dot"'), 2)

    def test_all_columns_render(self):
        response = self.client.get(self.URL)
        for header in ("Nombre del grupo", "Número de contactos", "Fecha", "Creado por"):
            self.assertContains(response, header)

    def test_no_checkbox_column_unlike_productos(self):
        html = self.client.get(self.URL).content.decode()
        self.assertNotIn('type="checkbox"', html)

    def test_empty_state_is_bare_centered_text(self):
        html = self.client.get(self.URL).content.decode()
        self.assertIn("Sin lista de clientes", html)
        empty = html.split('class="list-card__empty"', 1)[1].split("</tr>", 1)[0]
        self.assertIn('colspan="4"', empty)
        self.assertNotIn("<svg", empty)  # minimal, like the "Sin flujos" state

    def test_row_renders_name_count_date_and_author(self):
        ana = Client.objects.create(first_name="Ana", phone="+571", country="CO")
        luc = Client.objects.create(first_name="Luc", phone="+331", country="FR")
        vip = ClientList.objects.create(name="VIP", created_by="Samuel")
        vip.clients.set([ana, luc])

        html = self.client.get(self.URL).content.decode()
        self.assertIn("VIP", html)
        self.assertIn("Samuel", html)
        # localtime() mirrors the |date filter, so this holds under any TIME_ZONE.
        self.assertIn(timezone.localtime(vip.created_at).strftime("%d/%m/%Y"), html)
        row = html.split("VIP", 1)[1].split("</tr>", 1)[0]
        self.assertIn("<td>2</td>", row)  # the contact count, from the M2M
        self.assertNotIn("Sin lista de clientes", html)

    def test_create_button_points_at_a_real_route(self):
        html = self.client.get(self.URL).content.decode()
        self.assertIn(f'href="{reverse("lista_create")}"', html)
        self.assertIn(f'hx-get="{reverse("lista_create")}"', html)
        # "closest main" resolves under either mount (#crm-panel / #campanas-panel).
        self.assertIn('hx-target="closest main"', html)

    def test_create_route_returns_a_placeholder_panel(self):
        response = self.client.get(reverse("lista_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crear lista — próximamente")
