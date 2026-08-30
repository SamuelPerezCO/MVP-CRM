"""
Single source of truth for the sidebar navigation.

Everything about a nav item lives here: its URL slug, its tooltip label, which
SVG to draw and whether it shows a notification dot. The sidebar template just
loops over these lists, so adding / reordering / renaming a section is a one-line
change in this file -- no template edits required.

To flesh out a section later, drop a real template at
``templates/sections/<key>.html``. Until that file exists the view falls back to
``templates/sections/_placeholder.html`` automatically (see ``core.views``).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    """One icon in the sidebar."""

    key: str
    """URL slug and section-template name, e.g. ``inbox`` -> /s/inbox/."""

    label: str
    """Spanish label shown in the hover tooltip and used as the accessible name.

    The sidebar is icon-only, so this is the *only* text naming the control --
    it is rendered both as a visible tooltip and as the link's aria-label.
    """

    icon: str
    """Template path of the SVG glyph under ``templates/icons/``."""

    badge: bool = False
    """Draw a small red notification dot on the icon (Academy in the reference)."""

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


# --- Main navigation -------------------------------------------------------
# Order here is the order rendered, top to bottom.
PRIMARY_NAV = [
    NavItem("inbox",            "Inbox",                          "inbox"),
    NavItem("crm",              "CRM",                            "users"),
    NavItem("embudos",          "Embudos",                        "funnel"),
    NavItem("mi-comercio",      "Mi comercio",                    "store"),
    NavItem("campanas",         "Campañas",                       "megaphone"),
    NavItem("estadisticas",     "Estadísticas",                   "bar-chart"),
    NavItem("integraciones",    "Integraciones",                  "activity"),
    NavItem("mensajeria",       "Configuración de mensajería",    "settings"),
]

# Removed for now (2026-08-26), to restore just re-add the NavItem -- the
# Automatizaciones screen, views and templates are all still in place:
#   NavItem("automatizaciones", "Automatizaciones",            "bot")
#   NavItem("performance-hub",  "Performance HUB",             "gauge")
#   NavItem("crecimiento",      "Herramientas de Crecimiento", "trending-up")

# --- Secondary group -------------------------------------------------------
# Pinned to the bottom of the sidebar, separated by a divider.
SECONDARY_NAV = [
    NavItem("apps",     "Aplicaciones", "grid"),
    NavItem("academy",  "Academy",      "graduation-cap", badge=True),
    NavItem("recursos", "Recursos",     "book"),
]

ALL_NAV = PRIMARY_NAV + SECONDARY_NAV

# Fast lookup used by the view to validate the slug in the URL.
NAV_BY_KEY = {item.key: item for item in ALL_NAV}

#: Fallback slug for :func:`core.views.section` when none is supplied.
#:
#: The site root is *not* a section any more -- it renders the welcome screen
#: (``core.views.welcome``) with no icon selected, so nothing maps "/" onto this
#: key. See :data:`WELCOME_SHORTCUTS` for what the welcome screen links to.
DEFAULT_SECTION = "inbox"

#: Sections featured as quick-access cards on the welcome screen, in order.
#: Keys must exist in :data:`NAV_BY_KEY`; the view resolves them to NavItems so
#: the cards reuse the same label and glyph as the rail.
WELCOME_SHORTCUTS = ["inbox", "crm", "embudos"]
