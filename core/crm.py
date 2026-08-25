"""
Data definition for the CRM screen's secondary nav panel (column 2).

Same shape as core.nav and core.inbox: declare the rows once, let the templates
loop. Here the rows are grouped into collapsible sections, and each row swaps
the panel in column 3.
"""

from dataclasses import dataclass

from django.template import TemplateDoesNotExist
from django.template.loader import get_template


@dataclass(frozen=True)
class View:
    """One selectable page inside the CRM."""

    key: str
    """Slug used in the URL, e.g. ``clientes`` -> /s/crm/?view=clientes."""

    label: str
    """Visible Spanish label, also used as the panel heading."""


@dataclass(frozen=True)
class Section:
    """A collapsible group of views in the nav panel."""

    key: str
    title: str
    icon: str
    views: list[View]

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


SECTIONS = [
    Section(
        "gestion-clientes",
        "Gestión de clientes",
        "users",
        [
            View("clientes", "Clientes"),
            View("etiquetas", "Etiquetas"),
            View("lista-clientes", "Lista de clientes"),
            View("campos-personalizados", "Campos personalizados"),
            View("exportaciones", "Exportaciones"),
        ],
    ),
    Section(
        "calendario",
        "Calendario",
        "calendar",
        [
            View("mi-calendario", "Mi calendario"),
        ],
    ),
]

ALL_VIEWS = [view for section in SECTIONS for view in section.views]
VIEW_BY_KEY = {view.key: view for view in ALL_VIEWS}

#: Shown when the CRM section first opens.
DEFAULT_VIEW = "clientes"

#: Rendered for any view that has no panel template yet.
PLACEHOLDER_PANEL = "partials/crm/panels/_placeholder.html"


def panel_template(view_key: str) -> str:
    """Return ``partials/crm/panels/<view_key>.html`` if it exists, else the
    placeholder.

    Mirrors core.views._section_template: building out one of these pages means
    creating the file, with no view or URL change.
    """
    candidate = f"partials/crm/panels/{view_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_PANEL
    return candidate
