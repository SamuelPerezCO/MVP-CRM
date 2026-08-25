"""
Data definition for the Mi comercio screen's secondary nav panel (column 2)
and the Productos table's tabs.

Same shape as core.crm: collapsible Sections of Views, rendered by the shared
partials/side_nav_section.html component. New here is a standalone row below
the sections ("Configuración del comercio") and the Tab list the Productos
table filters by.
"""

from dataclasses import dataclass

from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from .models import Product


@dataclass(frozen=True)
class View:
    """One selectable page inside Mi comercio."""

    key: str
    """Slug used in the URL, e.g. ``productos`` -> /s/mi-comercio/?view=productos."""

    label: str
    """Visible Spanish label, also used as the panel heading."""


@dataclass(frozen=True)
class Section:
    """A collapsible group of views in the nav panel.

    Rendered by partials/side_nav_section.html -- the same component any other
    screen with a multi-section nav can include.
    """

    key: str
    title: str
    icon: str
    views: list[View]

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


@dataclass(frozen=True)
class SingleView(View):
    """A standalone nav row outside the collapsible sections.

    Carries its own icon (section children don't) and renders with a small
    trailing caret, per the reference.
    """

    icon: str = "settings"

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


SECTIONS = [
    Section(
        "catalogo",
        "Catálogo",
        "grid",
        [
            View("productos", "Productos"),
            View("inventario", "Inventario"),
            View("categorias", "Categorías"),
            View("marcas", "Marcas"),
        ],
    ),
    Section(
        "ordenes",
        "Órdenes",
        "shopping-cart",
        [
            View("mis-ordenes", "Mis órdenes"),
            View("campos-personalizados", "Campos personalizados"),
            View("canales-de-venta", "Canales de venta"),
            View("historial-descargas", "Historial de descargas"),
        ],
    ),
    Section(
        "pagos",
        "Pagos",
        "scan",
        [
            View("pagos-digitales", "Pagos digitales"),
        ],
    ),
    Section(
        "envios",
        "Envíos",
        "truck",
        [
            View("metodos-envio", "Métodos de envío"),
            View("costo-envio", "Costo de envío"),
        ],
    ),
]

#: Standalone row below the sections. The full label overflows the panel width
#: on purpose -- CSS truncates it with an ellipsis and a title tooltip shows
#: the whole thing, exactly like the reference.
STANDALONE = SingleView("configuracion-comercio", "Configuración del comercio")

ALL_VIEWS = [view for section in SECTIONS for view in section.views] + [STANDALONE]
VIEW_BY_KEY = {view.key: view for view in ALL_VIEWS}

#: Shown when the Mi comercio section first opens.
DEFAULT_VIEW = "productos"

#: Rendered for any view that has no panel template yet.
PLACEHOLDER_PANEL = "partials/comercio/panels/_placeholder.html"


def panel_template(view_key: str) -> str:
    """Return ``partials/comercio/panels/<view_key>.html`` if it exists, else
    the placeholder. Building out one of these pages means creating the file,
    with no view or URL change."""
    candidate = f"partials/comercio/panels/{view_key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_PANEL
    return candidate


# --- Productos tabs --------------------------------------------------------


@dataclass(frozen=True)
class Tab:
    """One filter tab above the Productos table."""

    key: str
    """Slug used in the URL, e.g. ``activos`` -> ?view=productos&tab=activos."""

    label: str
    """Visible Spanish label."""


#: Order here is the order rendered, left to right.
TABS = [
    Tab("todos", "Todos los productos"),
    Tab("activos", "Activos"),
    Tab("inactivos", "Inactivos"),
]

TAB_BY_KEY = {tab.key: tab for tab in TABS}

#: The reference opens on Activos, not Todos.
DEFAULT_TAB = "activos"

#: Tab key -> the Product.status it filters by. "todos" is absent: no filter.
_TAB_STATUS = {
    "activos": "activo",
    "inactivos": "inactivo",
}


def get_products(tab_key: str):
    """Return the products the given tab shows, as a real queryset.

    With an empty table this returns no rows and the template renders the bare
    header -- the reference's empty state. No seed data by design.
    """
    products = Product.objects.all()
    status = _TAB_STATUS.get(tab_key)
    if status is not None:
        products = products.filter(status=status)
    return products


#: Table header labels, in column order. The template loops these so the
#: header row and its info-dots stay in one place; the cell templates must
#: render values in this same order.
TABLE_COLUMNS = [
    "Nombre",
    "Stock",
    "Precio",
    "Categoría",
    "Marca",
    "Estado",
    "Sincronizado con",
]
