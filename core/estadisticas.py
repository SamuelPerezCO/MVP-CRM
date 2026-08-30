"""
Data definition for the Estadísticas screen: the secondary nav panel
(column 2) and the Mensajería stat cards.

Same shape as core.embudos and core.automatizaciones: a flat nav list, each
row carrying its own icon. New here is the optional ``badge`` on a row (the
"Alpha" pill on Atribuciones) and the CARDS list the Mensajería panel loops.
"""

from dataclasses import dataclass

from django.template import TemplateDoesNotExist
from django.template.loader import get_template


@dataclass(frozen=True)
class View:
    """One selectable page inside Estadísticas."""

    key: str
    """Slug used in the URL."""

    label: str
    """Visible Spanish label, also used as the panel heading."""

    icon: str
    """Template name under ``templates/icons/``."""

    badge: str = ""
    """Small pill rendered after the label ("Alpha" on Atribuciones)."""

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


#: Flat list -- order here is the order rendered.
VIEWS = [
    View("mensajeria", "Mensajería", "message-circle"),
    View("ventas", "Ventas", "receipt"),
    View("etiquetas", "Etiquetas", "tag"),
    View("embudos", "Embudos", "funnel"),
    View("atribuciones", "Atribuciones", "bar-chart", badge="Alpha"),
    View("temas-conversacion", "Temas de conversación", "list"),
    View("agentes-ia", "Agentes de IA", "briefcase"),
]

VIEW_BY_KEY = {view.key: view for view in VIEWS}

#: Shown when the Estadísticas section first opens.
DEFAULT_VIEW = "mensajeria"

#: Rendered for any view that has no panel template yet.
PLACEHOLDER_PANEL = "partials/estadisticas/panels/_placeholder.html"

#: Rendered for any Mensajería card whose detail screen isn't built yet.
PLACEHOLDER_CARD = "partials/estadisticas/panels/_card_detail.html"


def panel_template(view_key: str) -> str:
    """Return ``partials/estadisticas/panels/<view_key>.html`` if it exists,
    else the placeholder. Building out one of these pages means creating the
    file, with no view or URL change."""
    candidate = f"partials/estadisticas/panels/{view_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_PANEL
    return candidate


# --- Mensajería stat cards --------------------------------------------------


@dataclass(frozen=True)
class Card:
    """One clickable card on the Mensajería panel."""

    key: str
    """Slug used in the placeholder detail URL."""

    title: str
    text: str

    icon: str
    """An emoji glyph, not an SVG: the reference's card icons are the colored
    emoji themselves (multicolor chart, purple stopwatch...), which a
    single-color line icon can't reproduce."""


#: Order here is the order rendered, left to right.
CARDS = [
    Card(
        "volumen-mensajes",
        "Volumen de Mensajes",
        "Mensajes por plataforma, horarios pico y distribución por canal "
        "de comunicación.",
        "📊",
    ),
    Card(
        "tiempos-respuesta",
        "Tiempos de Respuesta",
        "Tiempo promedio, horario laboral y respuesta humana post-MIA. "
        "Identifica cuellos de botella en la atención.",
        "⏱️",
    ),
    Card(
        "rendimiento-agentes",
        "Rendimiento de Agentes",
        "Carga de trabajo, mensajes enviados y estado de conversaciones "
        "por agente.",
        "👥",
    ),
    Card(
        "perfil-clientes",
        "Perfil de Clientes",
        "Métricas de toda la cuenta en el período: primer contacto, "
        "recurrentes, leads y etiquetas.",
        "👤",
    ),
]

CARD_BY_KEY = {card.key: card for card in CARDS}


def card_template(card_key: str) -> str:
    """Return ``partials/estadisticas/cards/<card_key>.html`` if it exists,
    else the placeholder -- same stance as :func:`panel_template`, so
    building one of the four detail screens means adding a file (and any
    context builder it needs) rather than branching the view."""
    candidate = f"partials/estadisticas/cards/{card_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_CARD
    return candidate
