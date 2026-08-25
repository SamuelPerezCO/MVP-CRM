"""URL map for the shell: the root entry, one route per nav section, plus the
Inbox's column-level partial endpoints."""

from django.urls import path

from . import views

urlpatterns = [
    # Root is the welcome screen: the shell before any section is chosen. It is
    # deliberately not a `section` route, so no sidebar icon matches "/".
    path("", views.welcome, name="home"),
    # Inbox column 3, fetched on its own when a conversation filter is picked.
    path("inbox/list/<slug:filter_key>/", views.inbox_list, name="inbox_list"),
    # CRM column 3, fetched on its own when a secondary-nav page is picked.
    path("crm/panel/<slug:view_key>/", views.crm_panel, name="crm_panel"),
    # Embudos column 3, fetched on its own when a secondary-nav page is picked.
    path("embudos/panel/<slug:view_key>/", views.embudos_panel, name="embudos_panel"),
    # Placeholder target for the "Crear nuevo embudo" button.
    path("embudos/nuevo/", views.embudo_create, name="embudo_create"),
    # Automatizaciones column 3, fetched on its own when a secondary-nav page is picked.
    path(
        "automatizaciones/panel/<slug:view_key>/",
        views.automatizaciones_panel,
        name="automatizaciones_panel",
    ),
    # Placeholder target for the "+ Añadir flujo" button.
    path("automatizaciones/flujos/nuevo/", views.flujo_create, name="flujo_create"),
    # Mi comercio column 3, fetched on its own when a secondary-nav page is picked.
    path(
        "comercio/panel/<slug:view_key>/",
        views.comercio_panel,
        name="comercio_panel",
    ),
    # The Productos table region (tabs + rows), fetched on its own per tab.
    # "tab/" keeps the slug from swallowing the nuevo/importar routes below.
    path(
        "comercio/productos/tab/<slug:tab_key>/",
        views.productos_table,
        name="productos_table",
    ),
    # Placeholder targets for the "Crear +" and "Importar" buttons.
    path("comercio/productos/nuevo/", views.producto_create, name="producto_create"),
    path("comercio/productos/importar/", views.producto_import, name="producto_import"),
    # Every sidebar icon points here; <key> matches a NavItem.key from nav.py.
    path("s/<slug:key>/", views.section, name="section"),
]
