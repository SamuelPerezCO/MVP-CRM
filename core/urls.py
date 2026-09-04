"""URL map for the shell: the root entry, one route per nav section, plus the
Inbox's column-level partial endpoints."""

from django.urls import path

from . import views

urlpatterns = [
    # The app-wide login gate (core.middleware.LoginRequiredMiddleware).
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Public legal pages -- no session required (core.middleware), because
    # Meta will not publish an app whose privacy policy needs a login.
    path("privacidad/", views.privacy, name="privacy"),
    path("eliminacion-de-datos/", views.data_deletion, name="data_deletion"),
    # Root is the welcome screen: the shell before any section is chosen. It is
    # deliberately not a `section` route, so no sidebar icon matches "/".
    path("", views.welcome, name="home"),
    # Inbox column 3, fetched on its own when a conversation filter is picked
    # (and re-fetched by the list's poll).
    path("inbox/list/<slug:filter_key>/", views.inbox_list, name="inbox_list"),
    # One open conversation: columns 4 + 5 in a single response.
    path("inbox/chat/<int:conversation_id>/", views.inbox_chat, name="inbox_chat"),
    # Just the message thread, re-fetched by the open chat's poll.
    path(
        "inbox/chat/<int:conversation_id>/mensajes/",
        views.inbox_thread,
        name="inbox_thread",
    ),
    # The composer posts here; answers with the refreshed thread.
    path(
        "inbox/chat/<int:conversation_id>/enviar/",
        views.inbox_send,
        name="inbox_send",
    ),
    # A plantilla into an existing chat -- the closed composer's picker.
    path(
        "inbox/chat/<int:conversation_id>/plantilla/",
        views.inbox_send_template,
        name="inbox_send_template",
    ),
    # "Nuevo Chat": GET the modal body, POST to start a conversation.
    path("inbox/nuevo/", views.inbox_new_chat, name="inbox_new_chat"),
    # The chat header's agent dropdown posts here on every change.
    path(
        "inbox/chat/<int:conversation_id>/asignar/",
        views.inbox_assign,
        name="inbox_assign",
    ),
    # The composer's Respuestas rápidas popover, fetched when first opened.
    # Conversation-scoped because each entry posts itself to inbox_send.
    path(
        "inbox/chat/<int:conversation_id>/respuestas-rapidas/",
        views.inbox_quick_replies,
        name="inbox_quick_replies",
    ),
    # One conversation's tag picker: GET renders it, POST toggles/creates.
    path(
        "inbox/conversacion/<int:conversation_id>/etiquetas/",
        views.conversation_tags,
        name="conversation_tags",
    ),
    # Bulk add/remove of one tag over the checked conversations.
    path("inbox/etiquetas/bulk/", views.inbox_tags_bulk, name="inbox_tags_bulk"),
    # Etiquetas admin (CRM > Gestión de clientes > Etiquetas) mutations; all
    # answer with the re-rendered #tag-table region.
    path("crm/etiquetas/nueva/", views.tag_create, name="tag_create"),
    path("crm/etiquetas/<int:tag_id>/editar/", views.tag_update, name="tag_update"),
    path("crm/etiquetas/<int:tag_id>/archivo/", views.tag_archive, name="tag_archive"),
    # CRM column 3, fetched on its own when a secondary-nav page is picked.
    path("crm/panel/<slug:view_key>/", views.crm_panel, name="crm_panel"),
    # Mi calendario: the grid's JSON feed plus event/preference mutations,
    # fetched by static/js/calendario.js (not HTMX -- FullCalendar consumes).
    path("crm/calendario/eventos/", views.calendar_events, name="calendar_events"),
    path(
        "crm/calendario/eventos/nuevo/",
        views.calendar_event_create,
        name="calendar_event_create",
    ),
    path(
        "crm/calendario/eventos/<int:event_id>/editar/",
        views.calendar_event_update,
        name="calendar_event_update",
    ),
    path(
        "crm/calendario/eventos/<int:event_id>/mover/",
        views.calendar_event_move,
        name="calendar_event_move",
    ),
    path(
        "crm/calendario/eventos/<int:event_id>/eliminar/",
        views.calendar_event_delete,
        name="calendar_event_delete",
    ),
    path(
        "crm/calendario/preferencias/",
        views.calendar_prefs,
        name="calendar_prefs",
    ),
    # The client table region (rows + pager), fetched on its own when paging
    # and when the toolbar's search box changes.
    path("crm/clientes/tabla/", views.clientes_table, name="clientes_table"),
    # Clientes CRUD. Each one answers with a fragment for the shared modal in
    # the Clientes panel; the saves additionally swap the table in out-of-band.
    # Usuarios (CRM > Equipo): masters create/edit/deactivate teammates.
    path("crm/usuarios/nuevo/", views.usuario_form, name="usuario_create"),
    path("crm/usuarios/<int:user_id>/editar/", views.usuario_form, name="usuario_update"),
    path("crm/usuarios/<int:user_id>/activo/", views.usuario_active, name="usuario_active"),
    # The whole client base as an .xlsx download (CRM > Exportaciones).
    path("crm/clientes/exportar/", views.clientes_export, name="clientes_export"),
    path("crm/clientes/nuevo/", views.cliente_form, name="cliente_create"),
    path(
        "crm/clientes/<int:client_id>/editar/",
        views.cliente_form,
        name="cliente_update",
    ),
    path("crm/clientes/<int:client_id>/", views.cliente_detail, name="cliente_detail"),
    path(
        "crm/clientes/<int:client_id>/eliminar/",
        views.cliente_delete,
        name="cliente_delete",
    ),
    # Placeholder target for the "+ Crear lista" button.
    path("crm/listas/nueva/", views.lista_create, name="lista_create"),
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
    # Estadísticas column 3, fetched on its own when a secondary-nav page is picked.
    path(
        "estadisticas/panel/<slug:view_key>/",
        views.estadisticas_panel,
        name="estadisticas_panel",
    ),
    # Volumen de Mensajes: the report behind the period picker, fetched by
    # static/js/stats_volumen.js (not HTMX -- ECharts consumes the JSON).
    # Declared before the <card_key> route below, which would swallow it.
    path(
        "estadisticas/mensajeria/volumen/datos/",
        views.estadisticas_volumen_data,
        name="estadisticas_volumen_data",
    ),
    # Tiempos de Respuesta: same contract for its filter bar (period, agent,
    # platform), fetched by static/js/stats_tiempos.js.
    path(
        "estadisticas/mensajeria/tiempos/datos/",
        views.estadisticas_tiempos_data,
        name="estadisticas_tiempos_data",
    ),
    # One Mensajería stat card's detail screen (placeholder until its
    # template exists -- see core.estadisticas.card_template).
    path(
        "estadisticas/mensajeria/<slug:card_key>/",
        views.estadisticas_card,
        name="estadisticas_card",
    ),
    # Configuración de mensajería column 3, fetched per secondary-nav page.
    path(
        "mensajeria/panel/<slug:view_key>/",
        views.mensajeria_panel,
        name="mensajeria_panel",
    ),
    # The Plantillas table region (tabs + rows), fetched on its own per tab.
    # "tab/" keeps the slug from swallowing the galeria/editor routes below.
    path(
        "mensajeria/plantillas/tab/<slug:tab_key>/",
        views.plantillas_table,
        name="plantillas_table",
    ),
    # Respuestas rápidas CRUD (Configuración de mensajería). Each answers with
    # a fragment for the panel's shared modal; saves also swap the table.
    path("mensajeria/respuestas/nueva/", views.respuesta_form, name="respuesta_create"),
    path(
        "mensajeria/respuestas/<int:reply_id>/editar/",
        views.respuesta_form,
        name="respuesta_update",
    ),
    path(
        "mensajeria/respuestas/<int:reply_id>/eliminar/",
        views.respuesta_delete,
        name="respuesta_delete",
    ),
    path(
        "mensajeria/respuestas/<int:reply_id>/activa/",
        views.respuesta_toggle,
        name="respuesta_toggle",
    ),
    # "Sincronizar con WhatsApp": pull approval verdicts, answer with the table.
    path(
        "mensajeria/plantillas/sincronizar/",
        views.plantillas_sync,
        name="plantillas_sync",
    ),
    # The chooser modal's first card: pick a ready-made template (placeholder).
    path(
        "mensajeria/plantillas/galeria/",
        views.plantilla_gallery,
        name="plantilla_gallery",
    ),
    # The chooser modal's second card: the Crear plantilla editor. GET renders
    # it, POST validates and saves the template.
    path(
        "mensajeria/plantillas/editor/",
        views.plantilla_editor,
        name="plantilla_editor",
    ),
    # Every sidebar icon points here; <key> matches a NavItem.key from nav.py.
    path("s/<slug:key>/", views.section, name="section"),
]
