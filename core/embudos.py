"""
Data definition for the Embudos screen's secondary nav panel (column 2).

Same shape as core.crm, minus the grouping: this nav is a flat list, so the
rows carry their own icons rather than sitting under a collapsible header.
"""

from dataclasses import dataclass

from django.template import TemplateDoesNotExist
from django.template.loader import get_template


@dataclass(frozen=True)
class View:
    """One selectable page inside Embudos."""

    key: str
    """Slug used in the URL, e.g. ``embudos`` -> /s/embudos/?view=embudos."""

    label: str
    """Visible Spanish label, also used as the panel heading."""

    icon: str
    """Template name under ``templates/icons/``."""

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


#: Flat list -- order here is the order rendered.
VIEWS = [
    View("embudos", "Embudos", "briefcase"),
    View("automatizaciones", "Automatizaciones", "zap"),
    View("historial-descargas", "Historial de descargas", "download"),
]

VIEW_BY_KEY = {view.key: view for view in VIEWS}

#: Shown when the Embudos section first opens.
DEFAULT_VIEW = "embudos"

#: Rendered for any view that has no panel template yet.
PLACEHOLDER_PANEL = "partials/embudos/panels/_placeholder.html"


def panel_template(view_key: str) -> str:
    """Return ``partials/embudos/panels/<view_key>.html`` if it exists, else the
    placeholder. Building out one of these pages means creating the file."""
    candidate = f"partials/embudos/panels/{view_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_PANEL
    return candidate


def get_funnels():
    """Return the user's funnels.

    Placeholder for the real queryset -- there is no Funnel model yet. Returning
    an empty list is what makes the panel render its empty-state card; once
    funnels exist this becomes the real lookup and the panel's {% if %} starts
    taking the other branch, where the funnel list gets built.
    """
    return []
