"""
Data definition for the Automatizaciones screen's secondary nav panel (column 2).

Same shape as core.embudos: a flat list of rows, each carrying its own icon.
The one wrinkle is "MIA Agentes con IA", which is expandable -- it renders as a
<details> toggle with a slot for child rows that are not defined yet. Filling
``children`` in is all it will take to light them up; the template already
loops over it.
"""

from dataclasses import dataclass, field

from django.template import TemplateDoesNotExist
from django.template.loader import get_template


@dataclass(frozen=True)
class View:
    """One selectable page inside Automatizaciones."""

    key: str
    """Slug used in the URL, e.g. ``chatbots-flujo`` -> /s/automatizaciones/?view=chatbots-flujo."""

    label: str
    """Visible Spanish label, also used as the panel heading."""

    icon: str
    """Template name under ``templates/icons/``."""

    expandable: bool = False
    """Render as a collapsible <details> row with a chevron.

    The expanded slot lists ``children``; while that tuple is empty the toggle
    still works, it just reveals nothing (the sub-items are not known yet).
    """

    children: tuple = field(default=())
    """Future sub-views of an expandable row. Same ``View`` shape, no icon needed."""

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


#: Flat list -- order here is the order rendered.
VIEWS = [
    View("chatbots-flujo", "Chatbots de flujo", "git-fork"),
    View("mia-agentes", "MIA Agentes con IA", "briefcase", expandable=True),
    View("mensajes-programados", "Mensajes programados", "zap"),
    View("flujos-whatsapp", "Flujos de WhatsApp", "history"),
]

VIEW_BY_KEY = {view.key: view for view in VIEWS}

#: Shown when the Automatizaciones section first opens.
DEFAULT_VIEW = "chatbots-flujo"

#: Rendered for any view that has no panel template yet.
PLACEHOLDER_PANEL = "partials/automatizaciones/panels/_placeholder.html"


def panel_template(view_key: str) -> str:
    """Return ``partials/automatizaciones/panels/<view_key>.html`` if it exists,
    else the placeholder. Building out one of these pages means creating the
    file, with no view or URL change."""
    candidate = f"partials/automatizaciones/panels/{view_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_PANEL
    return candidate


def get_flows():
    """Return the user's WhatsApp flows.

    Placeholder for the real queryset -- there is no Flow model yet. Returning
    an empty list is what makes the panel render its "Sin flujos" empty state;
    once flows exist this becomes the real lookup and the panel's {% if %}
    starts taking the other branch, where the flow list gets built.
    """
    return []
