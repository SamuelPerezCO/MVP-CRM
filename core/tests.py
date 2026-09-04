"""Tests for the sidebar shell: routing, active state, and HTMX fragments."""

import pathlib

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from core.nav import (
    ALL_NAV,
    NAV_BY_KEY,
    PRIMARY_NAV,
    SECONDARY_NAV,
    WELCOME_SHORTCUTS,
)

HTMX = {"HX-Request": "true"}


def a_placeholder_section() -> str:
    """A nav key whose section still has no template of its own.

    Looked up rather than hardcoded: as screens get built, a hardcoded key
    silently starts testing the wrong thing.
    """
    from core.views import PLACEHOLDER_TEMPLATE, _section_template

    for item in ALL_NAV:
        if _section_template(item.key) == PLACEHOLDER_TEMPLATE:
            return item.key
    raise AssertionError("every section has a template now; this test needs rewriting")


class NavDefinitionTests(TestCase):
    def test_expected_item_counts(self):
        self.assertEqual(len(PRIMARY_NAV), 8)
        self.assertEqual(len(SECONDARY_NAV), 3)

    def test_keys_are_unique(self):
        keys = [item.key for item in ALL_NAV]
        self.assertEqual(len(keys), len(set(keys)))

    def test_only_academy_has_a_badge(self):
        badged = [item.key for item in ALL_NAV if item.badge]
        self.assertEqual(badged, ["academy"])

    def test_every_icon_template_exists(self):
        from django.template.loader import get_template

        for item in ALL_NAV:
            with self.subTest(item.key):
                get_template(item.icon_template)  # raises if missing


class RoutingTests(TestCase):
    def test_root_renders_the_welcome_screen(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["active_key"])

    def test_every_nav_key_resolves(self):
        for item in ALL_NAV:
            with self.subTest(item.key):
                response = self.client.get(reverse("section", args=[item.key]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["active_key"], item.key)

    def test_unknown_section_is_404(self):
        self.assertEqual(self.client.get("/s/does-not-exist/").status_code, 404)


class SidebarRenderTests(TestCase):
    def test_every_icon_renders_with_a_tooltip(self):
        html = self.client.get(reverse("home")).content.decode()
        for item in ALL_NAV:
            with self.subTest(item.key):
                # aria-label is the accessible name; the tooltip repeats it visually.
                self.assertIn(f'aria-label="{item.label}"', html)
                self.assertIn(f">{item.label}</span>", html)

    def test_active_item_gets_pill_and_aria_current(self):
        html = self.client.get(reverse("section", args=["campanas"])).content.decode()
        self.assertEqual(html.count("nav-item is-active"), 1)
        self.assertEqual(html.count('aria-current="page"'), 1)
        # ...and it is the right one: the active class sits on the campanas link.
        active = html.split('href="/s/campanas/"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_badge_renders_once(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertEqual(html.count("nav-item__badge"), 1)

    def test_items_are_real_links_so_the_rail_works_without_js(self):
        html = self.client.get(reverse("home")).content.decode()
        for item in ALL_NAV:
            with self.subTest(item.key):
                self.assertIn(f'href="/s/{item.key}/"', html)


class HtmxSwapTests(TestCase):
    def test_htmx_request_returns_a_bare_fragment(self):
        response = self.client.get(
            reverse("section", args=[a_placeholder_section()]), headers=HTMX
        )
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)          # no document shell
        self.assertNotIn("sidebar", body)        # sidebar is never re-sent
        self.assertIn("page-head__title", body)  # heading travels with the fragment

    def test_plain_request_returns_the_full_document(self):
        body = self.client.get(
            reverse("section", args=[a_placeholder_section()])
        ).content.decode()
        self.assertIn("<html", body)
        self.assertIn("sidebar", body)

    def test_fragment_and_full_page_agree_on_content(self):
        url = reverse("section", args=["crm"])
        fragment = self.client.get(url, headers=HTMX).content.decode()
        full = self.client.get(url).content.decode()
        self.assertIn(fragment.strip(), full)


class SectionTemplateTests(TestCase):
    def test_section_with_its_own_template_uses_it(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertEqual(response.context["section_template"], "sections/inbox.html")

    def test_section_without_a_template_falls_back_to_placeholder(self):
        key = a_placeholder_section()
        response = self.client.get(reverse("section", args=[key]))
        self.assertEqual(
            response.context["section_template"], "sections/_placeholder.html"
        )
        # Just the "próximamente" title -- the file hint was removed on request.
        self.assertContains(response, "próximamente")


class InboxScreenTests(TestCase):
    """The Inbox section: four columns, filters, and swappable empty states."""

    def test_inbox_renders_all_four_columns(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        for marker in ("inbox-nav", "list-panel", "chat-panel", "details-panel"):
            with self.subTest(marker):
                self.assertContains(response, marker)

    def test_every_filter_row_renders(self):
        from core.inbox import ALL_FILTERS

        html = self.client.get(reverse("section", args=["inbox"])).content.decode()
        for row in ALL_FILTERS:
            with self.subTest(row.key):
                self.assertIn(f"?filter={row.key}", html)
                self.assertIn(row.label, html)

    def test_group_titles_render(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        for title in ("Conversaciones", "MIA", "Canales"):
            self.assertContains(response, title)

    def test_todos_is_active_by_default(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertEqual(response.context["active_filter"], "todos")
        self.assertContains(response, "inbox-nav__row is-active")

    def test_filter_query_param_sets_the_active_row(self):
        response = self.client.get(reverse("section", args=["inbox"]), {"filter": "whatsapp"})
        self.assertEqual(response.context["active_filter"], "whatsapp")
        html = response.content.decode()
        # exactly one active row, and it is the WhatsApp one
        self.assertEqual(html.count("inbox-nav__row is-active"), 1)
        active = html.split('?filter=whatsapp"')[0].rsplit("<a ", 1)[-1]
        self.assertIn("is-active", active)

    def test_unknown_filter_falls_back_instead_of_404(self):
        response = self.client.get(reverse("section", args=["inbox"]), {"filter": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_filter"], "todos")

    def test_channels_show_a_count_badge_and_others_do_not(self):
        html = self.client.get(reverse("section", args=["inbox"])).content.decode()
        # 7 channels carry a count; the 6 conversation/MIA rows do not
        self.assertEqual(html.count("inbox-nav__count"), 7)

    def test_brand_icons_use_their_own_colours(self):
        html = self.client.get(reverse("section", args=["inbox"])).content.decode()
        for colour in ("#25D366", "#0084FF", "#1877F2", "#E4405F"):
            with self.subTest(colour):
                self.assertIn(colour, html)

    def test_all_three_empty_states_render(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertContains(response, "Tu inbox está vacío")
        self.assertContains(response, "Cuando lleguen mensajes los verás aquí.")
        self.assertContains(response, "Selecciona una conversación para comenzar a chatear")
        self.assertContains(
            response, "Selecciona una conversación para ver los detalles del cliente."
        )

    def test_footer_and_help_dock_render(self):
        response = self.client.get(reverse("section", args=["inbox"]))
        self.assertContains(response, "Estamos aquí para ayudarte.")
        self.assertContains(response, "¿Necesitas ayuda? Haz clic aquí")


class InboxListEndpointTests(TestCase):
    """Column 3 is fetched on its own when a filter is picked."""

    def test_returns_only_the_list_fragment(self):
        response = self.client.get(reverse("inbox_list", args=["whatsapp"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("inbox-nav", body)     # nav panel is not re-sent
        self.assertNotIn("chat-panel", body)    # neither are columns 4/5
        self.assertIn("Tu inbox está vacío", body)

    def test_every_filter_has_a_working_endpoint(self):
        from core.inbox import ALL_FILTERS

        for row in ALL_FILTERS:
            with self.subTest(row.key):
                response = self.client.get(reverse("inbox_list", args=[row.key]))
                self.assertEqual(response.status_code, 200)

    def test_unknown_filter_is_404(self):
        self.assertEqual(self.client.get("/inbox/list/bogus/").status_code, 404)

    def test_nav_rows_target_the_list_endpoint(self):
        html = self.client.get(reverse("section", args=["inbox"])).content.decode()
        self.assertIn('hx-target="#conv-list"', html)
        self.assertIn('hx-get="/inbox/list/whatsapp/"', html)
        # and push a reloadable URL, not the fragment endpoint
        self.assertIn('hx-push-url="/s/inbox/?filter=whatsapp"', html)

    def test_list_endpoint_output_matches_what_the_page_embeds(self):
        fragment = self.client.get(reverse("inbox_list", args=["todos"])).content.decode()
        page = self.client.get(reverse("section", args=["inbox"])).content.decode()
        self.assertIn(fragment.strip(), page)


class WelcomeScreenTests(TestCase):
    """The landing state at "/": full shell, centred copy, and an idle rail."""

    def test_no_icon_is_marked_active(self):
        html = self.client.get(reverse("home")).content.decode()
        # The whole point of the screen: the rail renders, but nothing is lit.
        self.assertIn("nav-item", html)
        self.assertNotIn("is-active", html)
        self.assertNotIn("aria-current", html)

    def test_root_matches_no_sidebar_href(self):
        """Guards the regression the welcome screen exists to avoid.

        shell.js lights up `.nav-item[href="<pathname>"]`, so if any icon ever
        pointed at "/" the rail would select itself on the welcome screen.
        """
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn('class="nav-item" href="/"', html)
        for item in ALL_NAV:
            with self.subTest(item.key):
                self.assertNotEqual(reverse("section", args=[item.key]), "/")

    def test_hero_copy_renders(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Bienvenido a MVP-CRM")
        self.assertContains(response, "Gestiona tus conversaciones, clientes y ventas")
        self.assertContains(response, "welcome__logo")

    def test_title_names_the_screen(self):
        self.assertContains(self.client.get(reverse("home")), "<title>Bienvenido · MVP CRM</title>")

    def test_shortcuts_link_to_their_sections(self):
        html = self.client.get(reverse("home")).content.decode()
        for key in WELCOME_SHORTCUTS:
            with self.subTest(key):
                url = reverse("section", args=[key])
                self.assertIn(f'href="{url}"', html)
                # Tells shell.js which icon to light up, since the card sits
                # outside the rail's [data-nav-group].
                self.assertIn(f'data-nav-for="{url}"', html)
                self.assertIn(NAV_BY_KEY[key].label, html)

    def test_footer_and_help_dock_render(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Estamos aquí para ayudarte.")
        self.assertContains(response, "¿Necesitas ayuda? Haz clic aquí")

    def test_htmx_request_returns_a_bare_fragment(self):
        body = self.client.get(reverse("home"), headers=HTMX).content.decode()
        self.assertNotIn("<html", body)
        self.assertNotIn("sidebar", body)
        self.assertIn("welcome__title", body)

    def test_leaving_the_welcome_screen_activates_that_icon(self):
        """Clicking through to a section restores the normal active state."""
        response = self.client.get(reverse("section", args=["crm"]))
        self.assertEqual(response.context["active_key"], "crm")
        self.assertEqual(response.content.decode().count("nav-item is-active"), 1)

class TemplateCommentLeakTests(TestCase):
    """Django's ``{#`` comments are single-line only: a multi-line one is not
    a comment at all and leaks into the page as visible text. Render every
    screen and pin that none does -- this has shipped twice before."""

    def test_no_section_leaks_template_comment_syntax(self):
        for item in ALL_NAV:
            with self.subTest(item.key):
                html = self.client.get(reverse("section", args=[item.key])).content.decode()
                self.assertNotIn("{#", html)
                self.assertNotIn("#}", html)

    def test_no_template_opens_a_comment_it_never_closes_on_that_line(self):
        """Django's {# #} comment does NOT span lines -- an unclosed one is
        printed to the page verbatim.

        Checked against the source rather than a rendered page because the
        test above cannot see this: the leak that prompted it sat inside
        {% for client in clients %}, and that loop has no rows to run with an
        empty database, so the comment never rendered and the page looked
        clean. Multi-line prose belongs in {% comment %}.
        """
        offenders = []
        for path in sorted(pathlib.Path(settings.BASE_DIR / "templates").rglob("*.html")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "{#" in line and "#}" not in line.split("{#", 1)[1]:
                    offenders.append(f"{path.name}:{number}: {line.strip()[:70]}")
        self.assertEqual(
            offenders, [],
            "These open {# without closing it on the same line, so Django "
            "renders them as page text:\n  " + "\n  ".join(offenders),
        )

    def test_welcome_screen_does_not_leak_either(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("{#", html)
        self.assertNotIn("#}", html)
