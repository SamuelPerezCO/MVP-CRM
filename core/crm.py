"""
Data definition for the "Mi cuenta" account nav (column 2) and its panels.

Same shape as core.nav and core.inbox: declare the rows once, let the templates
loop. Here the rows are grouped into collapsible sections, and each row swaps
the panel in column 3.

Two sidebar sections mount this same nav and the same panels -- the CRM and
Campañas (see sections/crm.html and sections/campanas.html): a panel built
here once lights up under both entry points.
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

    master_only: bool = False
    """Only masters (``User.is_superuser``, see core.agents) may open it.

    Hidden from the nav for everyone else -- and, since a hidden row is not
    a locked door, :func:`can_view` is what the views enforce.
    """


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
    Section(
        "equipo",
        "Equipo",
        "circle-user",
        [
            View("usuarios", "Usuarios", master_only=True),
        ],
    ),
]

ALL_VIEWS = [view for section in SECTIONS for view in section.views]
VIEW_BY_KEY = {view.key: view for view in ALL_VIEWS}


def can_view(user, view_key: str) -> bool:
    """Whether ``user`` may open ``view_key`` -- the one permission rule the
    CRM has: master-only views need a master, everything else is open."""
    view = VIEW_BY_KEY.get(view_key)
    if view is None:
        return False
    if not view.master_only:
        return True
    return bool(
        getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False)
    )


def visible_sections(user) -> list[Section]:
    """SECTIONS as ``user`` should see them: master-only views dropped for
    non-masters, and a section left with no views dropped with them (an
    empty collapsible would just be a puzzling header)."""
    visible = []
    for section in SECTIONS:
        views = [view for view in section.views if can_view(user, view.key)]
        if views:
            visible.append(Section(section.key, section.title, section.icon, views))
    return visible


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
