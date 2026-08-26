"""
Data definition for the Configuración de mensajería screen: the secondary nav
panel (column 2) and the Plantillas de WhatsApp tabs.

Same shape as core.estadisticas (flat nav) plus the tab list pattern from
core.comercio. One deliberate difference from the sibling navs: this panel has
no heading above the list, matching the reference.
"""

from dataclasses import dataclass

from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from .models import MessageTemplate


@dataclass(frozen=True)
class View:
    """One selectable page inside Configuración de mensajería."""

    key: str
    """Slug used in the URL."""

    label: str
    """Visible Spanish label, also used as the panel heading."""

    icon: str
    """Template name under ``templates/icons/``."""

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


#: Flat list -- order here is the order rendered.
VIEWS = [
    View("widget-whatsapp", "Widget de WhatsApp", "message-circle"),
    View("plantillas-whatsapp", "Plantillas de WhatsApp", "message-dots"),
    View("respuestas-rapidas", "Respuestas rápidas", "zap"),
    View("mensajes-bienvenida", "Mensajes de bienvenida", "messages-square"),
    View("temas-conversacion", "Temas de conversación", "list"),
    View("reglas-mensajeria", "Reglas de mensajería", "settings"),
    View("asignacion-automatica", "Asignación automática", "users"),
]

VIEW_BY_KEY = {view.key: view for view in VIEWS}

#: Shown when the Configuración de mensajería section first opens.
DEFAULT_VIEW = "plantillas-whatsapp"

#: Rendered for any view that has no panel template yet.
PLACEHOLDER_PANEL = "partials/mensajeria/panels/_placeholder.html"


def panel_template(view_key: str) -> str:
    """Return ``partials/mensajeria/panels/<view_key>.html`` if it exists,
    else the placeholder. Building out one of these pages means creating the
    file, with no view or URL change."""
    candidate = f"partials/mensajeria/panels/{view_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_PANEL
    return candidate


# --- Plantillas tabs --------------------------------------------------------


@dataclass(frozen=True)
class Tab:
    """One filter tab above the Plantillas table."""

    key: str
    label: str


#: Order here is the order rendered, left to right.
TABS = [
    Tab("todas", "Todas"),
    Tab("pendientes", "Pendientes"),
    Tab("aceptadas", "Aceptadas"),
    Tab("rechazadas", "Rechazadas"),
    Tab("desactivadas", "Desactivadas"),
]

TAB_BY_KEY = {tab.key: tab for tab in TABS}

DEFAULT_TAB = "todas"

#: Tab key -> the MessageTemplate.status it filters by. "todas" is absent (no
#: filter) and so is "desactivadas", which filters the is_active toggle
#: instead -- see the model docstring for why those are separate axes.
_TAB_STATUS = {
    "pendientes": "pendiente",
    "aceptadas": "aceptada",
    "rechazadas": "rechazada",
}

#: Table header labels, in column order. The template loops these; the cell
#: templates must render values in this same order.
TABLE_COLUMNS = ["Nombre", "Tipo", "Categoría", "Texto", "Equipo", "Activo", "Estado"]


def get_templates(tab_key: str):
    """Return the templates the given tab shows, as a real queryset.

    With an empty table this returns no rows and the panel renders the
    two-half empty state instead. No seed data by design.
    """
    templates = MessageTemplate.objects.all()
    if tab_key == "desactivadas":
        return templates.filter(is_active=False)
    status = _TAB_STATUS.get(tab_key)
    if status is not None:
        templates = templates.filter(status=status)
    return templates
