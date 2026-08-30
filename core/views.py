"""
Views for the single-page shell.

:func:`section` serves all 14 sidebar destinations. It answers two ways:

* a normal browser request  -> full page (base.html + sidebar + section)
* an HTMX request           -> just the section fragment, swapped into #content

That dual response is what makes the shell work without full page reloads while
keeping every URL directly linkable, bookmarkable and back-button friendly.

Sections that need their own data register a context builder in
:data:`SECTION_CONTEXT` rather than adding branches to ``section()``.

:func:`welcome` serves the root URL and answers the same two ways, but is not a
section: it is the shell at rest, with no sidebar icon selected.
"""

import hmac

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template import TemplateDoesNotExist
from django.template.loader import get_template, render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count

from messaging import services as messaging_services
from messaging.models import Conversation, Tag

from . import (
    automatizaciones,
    calendario,
    comercio,
    crm,
    embudos,
    estadisticas,
    estadisticas_volumen,
    inbox,
    mensajeria,
    plantillas,
)
from .middleware import SESSION_KEY
from .models import CalendarEvent, Client, ClientList, MessageTemplate
from .nav import (
    DEFAULT_SECTION,
    NAV_BY_KEY,
    PRIMARY_NAV,
    SECONDARY_NAV,
    WELCOME_SHORTCUTS,
)

#: Rendered when a section has no template of its own yet.
PLACEHOLDER_TEMPLATE = "sections/_placeholder.html"


def _section_template(key: str) -> str:
    """Return ``sections/<key>.html`` if it exists, else the placeholder.

    This is the extension point: to build out a section, create the file. No
    view, URL or nav change is needed -- it is picked up on the next request.
    """
    candidate = f"sections/{key}.html"
    try:
        get_template(candidate)
    except TemplateDoesNotExist:
        return PLACEHOLDER_TEMPLATE
    return candidate


def _is_htmx(request) -> bool:
    """True when HTMX issued the request and wants a fragment back."""
    return request.headers.get("HX-Request") == "true"


# --- Per-section context ---------------------------------------------------


def _thread_context(conversation) -> dict:
    """Context for the chat panel + details panel of one open conversation.

    Shared by the full-page render (``?chat=``), the HTMX chat swap and the
    thread poll, so all three always agree on what a thread looks like.
    """
    return {
        "active_conversation": conversation,
        "active_conversation_id": conversation.pk,
        "chat_messages": conversation.messages.all(),
        "window_open": conversation.is_within_24h_window,
    }


def _mark_read(conversation) -> None:
    """Viewing a thread clears its unread badge."""
    if conversation.unread_count:
        conversation.unread_count = 0
        conversation.save(update_fields=["unread_count"])


def _selected_tag_ids(data) -> list[int]:
    """The tag filter's selected ids out of a GET/POST QueryDict, ignoring
    anything non-numeric (hand-edited URLs shouldn't 500 the list)."""
    ids = []
    for raw in data.getlist("tags"):
        try:
            ids.append(int(raw))
        except ValueError:
            continue
    return ids


def _inbox_context(request) -> dict:
    """Extra context for the Inbox screen.

    ``?filter=`` selects the active row in the nav panel, ``?chat=`` the open
    conversation and ``?tags=`` (repeatable) the tag filter. Unknown values
    fall back to the default / no selection rather than 404-ing, so a stale
    bookmark still opens.
    """
    filter_key = request.GET.get("filter", inbox.DEFAULT_FILTER)
    if filter_key not in inbox.FILTER_BY_KEY:
        filter_key = inbox.DEFAULT_FILTER

    tag_ids = _selected_tag_ids(request.GET)

    context = {
        "filter_groups": inbox.FILTER_GROUPS,
        "active_filter": filter_key,
        "counts": inbox.get_counts(),
        "conversations": inbox.get_conversations(filter_key, request.user, tag_ids),
        # The tag-filter dropdown in column 3's toolbar.
        "all_tags": Tag.objects.filter(is_archived=False),
        "selected_tag_ids": tag_ids,
        "active_conversation": None,
        "active_conversation_id": None,
    }

    try:
        chat_id = int(request.GET.get("chat", ""))
    except ValueError:
        chat_id = None
    if chat_id is not None:
        conversation = (
            Conversation.objects.select_related("contact", "assigned_to")
            .filter(pk=chat_id)
            .first()
        )
        if conversation is not None:
            _mark_read(conversation)
            context.update(_thread_context(conversation))

    return context


#: How many clients per page in the CRM table.
CLIENTS_PER_PAGE = 25


def _clientes_context(request) -> dict:
    """Paginated client list for the CRM's Clientes table."""
    page = Paginator(Client.objects.all(), CLIENTS_PER_PAGE).get_page(
        request.GET.get("page")
    )
    return {"clients": page, "page_obj": page}


def _lista_clientes_context(request) -> dict:
    """Client lists for the Lista de clientes table.

    The contact count is annotated in one query rather than counted per row in
    the template; the template renders it as ``contact_count``.
    """
    return {
        "client_lists": ClientList.objects.annotate(contact_count=Count("clients")),
    }


def _etiquetas_context(request) -> dict:
    """Tags for the Etiquetas admin table, with usage counts.

    Usage is annotated in one query (never counted per row in the template),
    and archived tags sink below the active ones instead of disappearing --
    they still label old conversations, so they remain visible and restorable.
    """
    return {
        "tags": (
            Tag.objects.select_related("created_by")
            .annotate(usage=Count("conversation_tags"))
            .order_by("is_archived", "name")
        ),
        "tag_colors": Tag.COLOR_CHOICES,
    }


def _mi_calendario_context(request) -> dict:
    """Data for the Mi calendario panel: the sidebar preferences (session-
    kept until real users/auth exist) and the event modal's option lists."""
    return {
        "calendar_prefs": calendario.get_prefs(request.session),
        "event_types": calendario.EVENT_TYPES,
        "slot_choices": calendario.SLOT_CHOICES,
        "reminder_choices": calendario.REMINDER_CHOICES,
        "contacts": Client.objects.all(),
        "advisors": get_user_model().objects.filter(is_active=True).order_by("username"),
    }


#: CRM view key -> callable(request) -> dict. Panels without an entry need no data.
PANEL_CONTEXT = {
    "clientes": _clientes_context,
    "etiquetas": _etiquetas_context,
    "lista-clientes": _lista_clientes_context,
    "mi-calendario": _mi_calendario_context,
}


def _crm_context(request) -> dict:
    """Extra context for the screens that mount the account nav (CRM, Campañas).

    Both sections render the same core.crm sections and panels; only the
    section template differs (see sections/crm.html and sections/campanas.html).

    ``?view=`` selects the active row in the secondary nav. An unknown value
    falls back to the default rather than 404-ing, so a stale bookmark opens.
    """
    view_key = request.GET.get("view", crm.DEFAULT_VIEW)
    if view_key not in crm.VIEW_BY_KEY:
        view_key = crm.DEFAULT_VIEW

    context = {
        "crm_sections": crm.SECTIONS,
        "active_view": view_key,
        "crm_view": crm.VIEW_BY_KEY[view_key],
        "panel_template": crm.panel_template(view_key),
    }
    builder = PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))
    return context


def _embudos_panel_context(request) -> dict:
    """Data for the Embudos panel.

    ``funnels`` is what the panel branches on: empty renders the empty-state
    card, non-empty is where the funnel list will go.
    """
    return {"funnels": embudos.get_funnels()}


#: Embudos view key -> callable(request) -> dict. Views without an entry need no data.
EMBUDOS_PANEL_CONTEXT = {
    "embudos": _embudos_panel_context,
}


def _embudos_context(request) -> dict:
    """Extra context for the Embudos screen.

    ``?view=`` selects the active row in the secondary nav. An unknown value
    falls back to the default rather than 404-ing, so a stale bookmark opens.
    """
    view_key = request.GET.get("view", embudos.DEFAULT_VIEW)
    if view_key not in embudos.VIEW_BY_KEY:
        view_key = embudos.DEFAULT_VIEW

    context = {
        "embudos_nav": embudos.VIEWS,
        "active_view": view_key,
        "embudos_view": embudos.VIEW_BY_KEY[view_key],
        "panel_template": embudos.panel_template(view_key),
    }
    builder = EMBUDOS_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))
    return context


def _automatizaciones_panel_context(request) -> dict:
    """Data for the Chatbots de flujo (Flujos) panel.

    ``flows`` is what the panel branches on: empty renders the "Sin flujos"
    state, non-empty is where the flow list will go.
    """
    return {"flows": automatizaciones.get_flows()}


#: Automatizaciones view key -> callable(request) -> dict. Views without an
#: entry need no data.
AUTOM_PANEL_CONTEXT = {
    "chatbots-flujo": _automatizaciones_panel_context,
}


def _automatizaciones_context(request) -> dict:
    """Extra context for the Automatizaciones screen.

    ``?view=`` selects the active row in the secondary nav. An unknown value
    falls back to the default rather than 404-ing, so a stale bookmark opens.
    """
    view_key = request.GET.get("view", automatizaciones.DEFAULT_VIEW)
    if view_key not in automatizaciones.VIEW_BY_KEY:
        view_key = automatizaciones.DEFAULT_VIEW

    context = {
        "autom_nav": automatizaciones.VIEWS,
        "active_view": view_key,
        "autom_view": automatizaciones.VIEW_BY_KEY[view_key],
        "panel_template": automatizaciones.panel_template(view_key),
    }
    builder = AUTOM_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))
    return context


def _productos_context(request) -> dict:
    """Data for the Productos panel: the tab row and the filtered queryset.

    ``?tab=`` selects the active tab. An unknown value falls back to the
    default rather than 404-ing, so a stale bookmark still opens.
    """
    tab_key = request.GET.get("tab", comercio.DEFAULT_TAB)
    if tab_key not in comercio.TAB_BY_KEY:
        tab_key = comercio.DEFAULT_TAB

    return {
        "tabs": comercio.TABS,
        "active_tab": tab_key,
        "columns": comercio.TABLE_COLUMNS,
        "products": comercio.get_products(tab_key),
    }


#: Mi comercio view key -> callable(request) -> dict. Views without an entry
#: need no data.
COMERCIO_PANEL_CONTEXT = {
    "productos": _productos_context,
}


def _comercio_context(request) -> dict:
    """Extra context for the Mi comercio screen.

    ``?view=`` selects the active row in the secondary nav. An unknown value
    falls back to the default rather than 404-ing, so a stale bookmark opens.
    """
    view_key = request.GET.get("view", comercio.DEFAULT_VIEW)
    if view_key not in comercio.VIEW_BY_KEY:
        view_key = comercio.DEFAULT_VIEW

    context = {
        "comercio_sections": comercio.SECTIONS,
        "comercio_single": comercio.STANDALONE,
        "active_view": view_key,
        "comercio_view": comercio.VIEW_BY_KEY[view_key],
        "panel_template": comercio.panel_template(view_key),
    }
    builder = COMERCIO_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))
    return context


def _mensajeria_stats_context(request) -> dict:
    """Data for the Mensajería panel: the four stat cards."""
    return {"stat_cards": estadisticas.CARDS}


#: Estadísticas view key -> callable(request) -> dict. Views without an entry
#: need no data.
STATS_PANEL_CONTEXT = {
    "mensajeria": _mensajeria_stats_context,
}


def _estadisticas_context(request) -> dict:
    """Extra context for the Estadísticas screen.

    ``?view=`` selects the active row in the secondary nav. An unknown value
    falls back to the default rather than 404-ing, so a stale bookmark opens.
    """
    view_key = request.GET.get("view", estadisticas.DEFAULT_VIEW)
    if view_key not in estadisticas.VIEW_BY_KEY:
        view_key = estadisticas.DEFAULT_VIEW

    context = {
        "stats_nav": estadisticas.VIEWS,
        "active_view": view_key,
        "stats_view": estadisticas.VIEW_BY_KEY[view_key],
        "panel_template": estadisticas.panel_template(view_key),
    }
    builder = STATS_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))
    return context


def _plantillas_context(request) -> dict:
    """Data for the Plantillas de WhatsApp panel: the tab row and the
    filtered queryset.

    ``?tab=`` selects the active tab. An unknown value falls back to the
    default rather than 404-ing, so a stale bookmark still opens.
    """
    tab_key = request.GET.get("tab", mensajeria.DEFAULT_TAB)
    if tab_key not in mensajeria.TAB_BY_KEY:
        tab_key = mensajeria.DEFAULT_TAB

    return {
        "tabs": mensajeria.TABS,
        "active_tab": tab_key,
        "columns": mensajeria.TABLE_COLUMNS,
        "templates": mensajeria.get_templates(tab_key),
    }


#: Configuración de mensajería view key -> callable(request) -> dict. Views
#: without an entry need no data.
MENSAJERIA_PANEL_CONTEXT = {
    "plantillas-whatsapp": _plantillas_context,
}


def _mensajeria_context(request) -> dict:
    """Extra context for the Configuración de mensajería screen.

    ``?view=`` selects the active row in the secondary nav. An unknown value
    falls back to the default rather than 404-ing, so a stale bookmark opens.
    """
    view_key = request.GET.get("view", mensajeria.DEFAULT_VIEW)
    if view_key not in mensajeria.VIEW_BY_KEY:
        view_key = mensajeria.DEFAULT_VIEW

    context = {
        "msg_nav": mensajeria.VIEWS,
        "active_view": view_key,
        "msg_view": mensajeria.VIEW_BY_KEY[view_key],
        "panel_template": mensajeria.panel_template(view_key),
    }
    builder = MENSAJERIA_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))
    return context


#: section key -> callable(request) -> dict of extra template context.
SECTION_CONTEXT = {
    "inbox": _inbox_context,
    "crm": _crm_context,
    # Campañas mounts the same account nav and panels as the CRM (core.crm).
    "campanas": _crm_context,
    "embudos": _embudos_context,
    "automatizaciones": _automatizaciones_context,
    "mi-comercio": _comercio_context,
    "estadisticas": _estadisticas_context,
    "mensajeria": _mensajeria_context,
}


# --- Views -----------------------------------------------------------------


def login_view(request):
    """The one gate in front of the whole app -- see core.middleware."""
    error = None
    next_url = request.GET.get('next') or request.POST.get('next') or reverse('home')
    # ?next is attacker-reachable (it's a query param on a link anyone can send)
    # -- without this check a crafted next=https://evil.example would redirect
    # a successful login straight off the site, the classic post-login
    # open-redirect phishing setup.
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse('home')
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        valid = (
            bool(settings.APP_LOGIN_USERNAME)
            and hmac.compare_digest(username, settings.APP_LOGIN_USERNAME)
            and hmac.compare_digest(password, settings.APP_LOGIN_PASSWORD)
        )
        if valid:
            request.session[SESSION_KEY] = True
            return redirect(next_url)
        error = 'Usuario o contraseña incorrectos.'
    return HttpResponse(
        render_to_string(
            'login.html', {'error': error, 'next': next_url}, request=request
        )
    )


def logout_view(request):
    request.session.flush()
    return redirect('login')


def welcome(request):
    """Render the landing screen served at "/".

    This is the shell's resting state: the app is open but no section has been
    picked yet. The only thing that makes it special is ``active_key=None`` --
    :file:`partials/nav_item.html` compares every ``item.key`` against it, so a
    value no key can equal leaves the whole rail unselected.

    It deliberately does *not* go through :func:`section`: the root URL is not a
    nav destination, and keeping it separate is what stops "/" from resolving to
    a section and lighting up an icon.
    """
    context = {
        "primary_nav": PRIMARY_NAV,
        "secondary_nav": SECONDARY_NAV,
        "active_key": None,         # no icon is selected on the welcome screen
        "page_title": "Bienvenido",
        "shortcuts": [NAV_BY_KEY[key] for key in WELCOME_SHORTCUTS],
        "section_template": "sections/welcome.html",
    }

    if _is_htmx(request):
        return HttpResponse(
            render_to_string(context["section_template"], context, request=request)
        )

    return HttpResponse(render_to_string("base.html", context, request=request))


def section(request, key: str = DEFAULT_SECTION):
    """Render one nav section, as a fragment for HTMX or a full page otherwise."""
    item = NAV_BY_KEY.get(key)
    if item is None:
        raise Http404(f"Unknown section: {key!r}")

    context = {
        "primary_nav": PRIMARY_NAV,
        "secondary_nav": SECONDARY_NAV,
        "active_key": key,          # drives the active pill in the sidebar
        "item": item,               # the section's own label / icon
        "page_title": item.label,
        "section_template": _section_template(key),
    }

    # Let the section contribute its own data, if it registered a builder.
    builder = SECTION_CONTEXT.get(key)
    if builder is not None:
        context.update(builder(request))

    if _is_htmx(request):
        # Fragment only -- hx-swap="innerHTML" drops this inside <main id="content">.
        return HttpResponse(
            render_to_string(context["section_template"], context, request=request)
        )

    return HttpResponse(render_to_string("base.html", context, request=request))


def inbox_list(request, filter_key: str):
    """Return just the conversation list (Inbox column 3) for one filter.

    Targeted by the nav panel's HTMX requests (picking a filter) and by the
    list's own 5-second poll, which is how new inbound conversations appear
    without a refresh. ``?active=`` carries the open conversation's id so a
    poll re-render keeps its row highlighted.
    """
    if filter_key not in inbox.FILTER_BY_KEY:
        raise Http404(f"Unknown filter: {filter_key!r}")

    # Let the fake provider deliver due receipts even when no chat is open,
    # so tick marks keep moving while only the list is polling. No-op on
    # real (push-based) providers.
    messaging_services.pump_provider_events()

    try:
        active_id = int(request.GET.get("active", ""))
    except ValueError:
        active_id = None

    return HttpResponse(
        render_to_string(
            "partials/inbox/conversation_list.html",
            {
                "active_filter": filter_key,
                "conversations": inbox.get_conversations(
                    filter_key, request.user, _selected_tag_ids(request.GET)
                ),
                "active_conversation_id": active_id,
            },
            request=request,
        )
    )


def inbox_chat(request, conversation_id: int):
    """Open one conversation: chat panel content plus, out-of-band, the
    details panel -- one click updates both columns in a single response.

    Targeted by the conversation rows' HTMX requests into #chat-panel; the
    ``hx-swap-oob`` block inside the template lands in #details-panel.
    """
    conversation = get_object_or_404(
        Conversation.objects.select_related("contact", "assigned_to"),
        pk=conversation_id,
    )
    _mark_read(conversation)

    return HttpResponse(
        render_to_string(
            "partials/inbox/chat_swap.html", _thread_context(conversation), request=request
        )
    )


def inbox_thread(request, conversation_id: int):
    """Return just the message list of one conversation.

    Targeted by the open thread's 5-second poll, which is how replies (and
    the fake provider's delivery receipts) appear without a refresh. Polling
    swaps only #chat-messages, so a half-typed draft in the composer below
    is never clobbered.
    """
    conversation = get_object_or_404(Conversation, pk=conversation_id)

    # Pull-based providers (the fake one) deliver their pending status events
    # on this tick; the thread then renders with fresh tick marks.
    messaging_services.pump_provider_events()

    # Still looking at the thread -- an inbound that arrived since the last
    # poll is read the moment it renders.
    _mark_read(conversation)

    return HttpResponse(
        render_to_string(
            "partials/inbox/chat_messages.html",
            _thread_context(conversation),
            request=request,
        )
    )


def inbox_send(request, conversation_id: int):
    """Send the composer's message, answering with the refreshed thread.

    The 24h-window rule is enforced in ``messaging.services.send_message``;
    the composer isn't even rendered when the window is closed, so tripping
    it here means the window expired while the thread was open -- answered
    with an inline notice rather than a broken send.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    conversation = get_object_or_404(
        Conversation.objects.select_related("contact"), pk=conversation_id
    )

    body = (request.POST.get("body") or "").strip()
    send_error = None
    if body:
        try:
            messaging_services.send_message(conversation, body, request.user)
        except messaging_services.SendWindowClosed:
            send_error = (
                "La ventana de 24 horas se cerró. Envía una plantilla "
                "aprobada para reabrir la conversación."
            )
        except messaging_services.SendFailed:
            send_error = "No se pudo enviar el mensaje. Inténtalo de nuevo."

    context = _thread_context(conversation)
    context["send_error"] = send_error
    return HttpResponse(
        render_to_string("partials/inbox/chat_messages.html", context, request=request)
    )


# --- Tags -------------------------------------------------------------------


def _picker_context(request, conversation, error=None) -> dict:
    """Context for the tag-picker panel of one conversation.

    ``?q=`` narrows the tag list; when it matches nothing exactly, the panel
    offers to create «q» inline. Archived tags never appear -- they exist
    only as history on already-tagged chats.
    """
    q = (request.GET.get("q") or request.POST.get("q") or "").strip()
    tags = Tag.objects.filter(is_archived=False)
    if q:
        tags = tags.filter(name__icontains=q)
    return {
        "conversation": conversation,
        "picker_tags": tags,
        "applied_ids": set(conversation.tags.values_list("pk", flat=True)),
        "q": q,
        "offer_create": bool(q) and not Tag.objects.filter(name__iexact=q).exists(),
        "tag_error": error,
    }


def conversation_tags(request, conversation_id: int):
    """The tag picker for one conversation: GET renders it, POST mutates.

    POST either toggles an existing tag (``tag_id`` + ``action`` add/remove)
    or creates-and-applies a brand-new one (``new_name``, the picker's inline
    «Crear ...» path). The response is the refreshed picker plus out-of-band
    updates of that conversation's pill rows (list row and chat header), so
    all three surfaces agree immediately.
    """
    conversation = get_object_or_404(Conversation, pk=conversation_id)

    if request.method == "POST":
        error = None
        new_name = (request.POST.get("new_name") or "").strip()
        try:
            if new_name:
                # Color is auto-assigned here (rotating palette); the user
                # can restyle it later in CRM > Etiquetas.
                tag = messaging_services.create_tag(new_name, user=request.user)
                messaging_services.apply_tag([conversation], tag, request.user)
            else:
                tag = get_object_or_404(Tag, pk=request.POST.get("tag_id"))
                if request.POST.get("action") == "remove":
                    messaging_services.remove_tag([conversation], tag)
                else:
                    messaging_services.apply_tag([conversation], tag, request.user)
        except (messaging_services.TagNameTaken, ValueError) as exc:
            error = str(exc)

        context = _picker_context(request, conversation, error)
        context["oob_pills"] = True
        return HttpResponse(
            render_to_string("partials/tags/picker_panel.html", context, request=request)
        )

    return HttpResponse(
        render_to_string(
            "partials/tags/picker_panel.html",
            _picker_context(request, conversation),
            request=request,
        )
    )


def inbox_tags_bulk(request):
    """Apply/remove one tag on every checked conversation at once.

    Posted from the list's "Acciones" dropdown; the enclosing form carries
    the checked ids (``selected``), the current nav filter (hidden input kept
    fresh by every list render) and the tag-filter state, so the response is
    the re-rendered list under exactly the filters the user was looking at.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tag = get_object_or_404(Tag, pk=request.POST.get("tag_id"))
    ids = [i for i in request.POST.getlist("selected") if i.isdigit()]
    conversations = Conversation.objects.filter(pk__in=ids)

    if request.POST.get("action") == "remove":
        messaging_services.remove_tag(conversations, tag)
    else:
        messaging_services.apply_tag(conversations, tag, request.user)

    filter_key = request.POST.get("filter", inbox.DEFAULT_FILTER)
    if filter_key not in inbox.FILTER_BY_KEY:
        filter_key = inbox.DEFAULT_FILTER
    try:
        active_id = int(request.POST.get("active", ""))
    except ValueError:
        active_id = None

    return HttpResponse(
        render_to_string(
            "partials/inbox/conversation_list.html",
            {
                "active_filter": filter_key,
                "conversations": inbox.get_conversations(
                    filter_key, request.user, _selected_tag_ids(request.POST)
                ),
                "active_conversation_id": active_id,
            },
            request=request,
        )
    )


def _tag_table_response(request, error=None):
    """The re-rendered #tag-table region every tag mutation answers with."""
    context = _etiquetas_context(request)
    context["tag_error"] = error
    return HttpResponse(
        render_to_string("partials/crm/tag_table.html", context, request=request)
    )


def tag_create(request):
    """Create a tag from the Etiquetas page's modal."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    error = None
    try:
        messaging_services.create_tag(
            request.POST.get("name", ""), request.POST.get("color") or None, request.user
        )
    except (messaging_services.TagNameTaken, ValueError) as exc:
        error = str(exc)
    return _tag_table_response(request, error)


def tag_update(request, tag_id: int):
    """Rename/recolor a tag -- every pill in the app updates with it."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tag = get_object_or_404(Tag, pk=tag_id)
    error = None
    try:
        messaging_services.update_tag(
            tag, request.POST.get("name", ""), request.POST.get("color", tag.color)
        )
    except (messaging_services.TagNameTaken, ValueError) as exc:
        error = str(exc)
    return _tag_table_response(request, error)


def tag_archive(request, tag_id: int):
    """Archive (``archived=1``) or restore (``archived=0``) a tag.

    There is deliberately no hard delete: archiving hides the tag from
    pickers while every already-tagged conversation keeps it.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tag = get_object_or_404(Tag, pk=tag_id)
    messaging_services.set_tag_archived(tag, request.POST.get("archived") == "1")
    return _tag_table_response(request)


def crm_panel(request, view_key: str):
    """Return just the CRM's column 3 for one secondary-nav view.

    Targeted by the nav panel's HTMX requests so picking a page re-renders only
    the panel, leaving the nav (and its collapsed/expanded state) untouched.
    """
    if view_key not in crm.VIEW_BY_KEY:
        raise Http404(f"Unknown CRM view: {view_key!r}")

    context = {
        "active_view": view_key,
        "crm_view": crm.VIEW_BY_KEY[view_key],
    }
    builder = PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(crm.panel_template(view_key), context, request=request)
    )


def clientes_table(request):
    """Return just the client table region (rows + pager) for one ``?page=``.

    Targeted by the pager's HTMX requests so paging re-renders only
    #client-table -- the title and toolbar above it stay put. The pager used
    to fetch the *full* Clientes panel into that region, nesting a second
    toolbar and a duplicate #client-table inside the first on every click.
    """
    return HttpResponse(
        render_to_string(
            "partials/crm/client_table.html", _clientes_context(request), request=request
        )
    )


def lista_create(request):
    """Placeholder destination for the "+ Crear lista" button.

    Wired so the button goes somewhere real; the creation flow itself is not
    built yet.
    """
    return HttpResponse(
        render_to_string("partials/crm/panels/_nueva_lista.html", {}, request=request)
    )


def embudos_panel(request, view_key: str):
    """Return just the Embudos column 3 for one secondary-nav view.

    Targeted by the nav panel's HTMX requests so picking a page re-renders only
    the panel, leaving the nav untouched.
    """
    if view_key not in embudos.VIEW_BY_KEY:
        raise Http404(f"Unknown Embudos view: {view_key!r}")

    context = {
        "active_view": view_key,
        "embudos_view": embudos.VIEW_BY_KEY[view_key],
    }
    builder = EMBUDOS_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(embudos.panel_template(view_key), context, request=request)
    )


def embudo_create(request):
    """Placeholder destination for the "Crear nuevo embudo" button.

    Wired so the button goes somewhere real; the creation flow itself is not
    built yet.
    """
    return HttpResponse(
        render_to_string("partials/embudos/panels/_nuevo.html", {}, request=request)
    )


def automatizaciones_panel(request, view_key: str):
    """Return just the Automatizaciones column 3 for one secondary-nav view.

    Targeted by the nav panel's HTMX requests so picking a page re-renders only
    the panel, leaving the nav (and its expanded/collapsed state) untouched.
    """
    if view_key not in automatizaciones.VIEW_BY_KEY:
        raise Http404(f"Unknown Automatizaciones view: {view_key!r}")

    context = {
        "active_view": view_key,
        "autom_view": automatizaciones.VIEW_BY_KEY[view_key],
    }
    builder = AUTOM_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(
            automatizaciones.panel_template(view_key), context, request=request
        )
    )


def flujo_create(request):
    """Placeholder destination for the "+ Añadir flujo" button.

    Wired so the button goes somewhere real; the creation flow itself is not
    built yet.
    """
    return HttpResponse(
        render_to_string(
            "partials/automatizaciones/panels/_nuevo_flujo.html", {}, request=request
        )
    )


def comercio_panel(request, view_key: str):
    """Return just the Mi comercio column 3 for one secondary-nav view.

    Targeted by the nav panel's HTMX requests so picking a page re-renders only
    the panel, leaving the nav (and its collapsed/expanded state) untouched.
    """
    if view_key not in comercio.VIEW_BY_KEY:
        raise Http404(f"Unknown Mi comercio view: {view_key!r}")

    context = {
        "active_view": view_key,
        "comercio_view": comercio.VIEW_BY_KEY[view_key],
    }
    builder = COMERCIO_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(comercio.panel_template(view_key), context, request=request)
    )


def productos_table(request, tab_key: str):
    """Return just the tabbed table region (tabs + rows) for one Productos tab.

    Targeted by the tab row's HTMX requests so picking a tab re-renders only
    #product-table -- the title and toolbar above it stay put, like the CRM
    pager swapping #client-table.
    """
    if tab_key not in comercio.TAB_BY_KEY:
        raise Http404(f"Unknown Productos tab: {tab_key!r}")

    return HttpResponse(
        render_to_string(
            "partials/comercio/product_table.html",
            {
                "tabs": comercio.TABS,
                "active_tab": tab_key,
                "columns": comercio.TABLE_COLUMNS,
                "products": comercio.get_products(tab_key),
            },
            request=request,
        )
    )


def producto_create(request):
    """Placeholder destination for the "Crear +" button.

    Wired so the button goes somewhere real; the creation flow itself is not
    built yet.
    """
    return HttpResponse(
        render_to_string("partials/comercio/panels/_crear.html", {}, request=request)
    )


def producto_import(request):
    """Placeholder destination for the "Importar" button.

    Wired so the button goes somewhere real; the import flow itself is not
    built yet.
    """
    return HttpResponse(
        render_to_string("partials/comercio/panels/_importar.html", {}, request=request)
    )


def estadisticas_panel(request, view_key: str):
    """Return just the Estadísticas column 3 for one secondary-nav view.

    Targeted by the nav panel's HTMX requests so picking a page re-renders only
    the panel, leaving the nav untouched.
    """
    if view_key not in estadisticas.VIEW_BY_KEY:
        raise Http404(f"Unknown Estadísticas view: {view_key!r}")

    context = {
        "active_view": view_key,
        "stats_view": estadisticas.VIEW_BY_KEY[view_key],
    }
    builder = STATS_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(
            estadisticas.panel_template(view_key), context, request=request
        )
    )


def _volumen_context(request) -> dict:
    """Data for the Volumen de Mensajes detail screen.

    The whole screen is governed by one period, so the range is parsed once
    here and the first render ships its report inline -- the page is useful
    before any JS runs. Changing the picker re-fetches
    :func:`estadisticas_volumen_data` instead of re-rendering the panel.
    """
    start, end = estadisticas_volumen.parse_range(request.GET)
    return {
        "period_start": start,
        "period_end": end,
        "period_label": estadisticas_volumen.format_range(start, end),
        "report": estadisticas_volumen.report(start, end),
        "channels": estadisticas_volumen.CHANNELS,
    }


#: Stat card key -> callable(request) -> dict, mirroring
#: :data:`STATS_PANEL_CONTEXT`. Cards without an entry render the
#: placeholder and need no data.
STATS_CARD_CONTEXT = {
    "volumen-mensajes": _volumen_context,
}


def estadisticas_card(request, card_key: str):
    """One Mensajería stat card's detail screen.

    Which template renders is resolved by name
    (:func:`estadisticas.card_template`), so the three cards still on the
    placeholder become real screens by adding a template plus a context
    builder above -- no branching here.
    """
    card = estadisticas.CARD_BY_KEY.get(card_key)
    if card is None:
        raise Http404(f"Unknown stat card: {card_key!r}")

    context = {"stat_card": card}
    builder = STATS_CARD_CONTEXT.get(card_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(
            estadisticas.card_template(card_key), context, request=request
        )
    )


def estadisticas_volumen_data(request):
    """JSON feed behind the Volumen de Mensajes screen's period picker.

    Answers the same report the panel renders inline, so moving the picker
    updates the tiles, the chart and the table from one request instead of
    re-rendering (and re-mounting) the whole screen. An unusable range falls
    back to the default window rather than erroring -- the response echoes
    the range it actually used so the picker can correct itself.
    """
    start, end = estadisticas_volumen.parse_range(request.GET)
    return JsonResponse(estadisticas_volumen.report(start, end))


def mensajeria_panel(request, view_key: str):
    """Return just the Configuración de mensajería column 3 for one
    secondary-nav view.

    Targeted by the nav panel's HTMX requests so picking a page re-renders only
    the panel, leaving the nav untouched.
    """
    if view_key not in mensajeria.VIEW_BY_KEY:
        raise Http404(f"Unknown Mensajería view: {view_key!r}")

    context = {
        "active_view": view_key,
        "msg_view": mensajeria.VIEW_BY_KEY[view_key],
    }
    builder = MENSAJERIA_PANEL_CONTEXT.get(view_key)
    if builder is not None:
        context.update(builder(request))

    return HttpResponse(
        render_to_string(mensajeria.panel_template(view_key), context, request=request)
    )


def plantillas_table(request, tab_key: str):
    """Return just the tabbed table region (tabs + rows) for one Plantillas tab.

    Targeted by the tab row's HTMX requests so picking a tab re-renders only
    #template-table -- the toolbar above it stays put, like the Productos tabs.
    """
    if tab_key not in mensajeria.TAB_BY_KEY:
        raise Http404(f"Unknown Plantillas tab: {tab_key!r}")

    return HttpResponse(
        render_to_string(
            "partials/mensajeria/template_table.html",
            {
                "tabs": mensajeria.TABS,
                "active_tab": tab_key,
                "columns": mensajeria.TABLE_COLUMNS,
                "templates": mensajeria.get_templates(tab_key),
            },
            request=request,
        )
    )


def _mensajeria_page(request, panel_template: str, panel_context: dict) -> HttpResponse:
    """The full mensajería page (base.html + sidebar + section) with the
    Plantillas panel swapped for ``panel_template``.

    Plain (non-HTMX) navigation lands here -- open-in-new-tab on a chooser
    card, or a no-JS form POST -- so those URLs render inside the shell
    instead of as a bare fragment. Mirrors section().
    """
    item = NAV_BY_KEY["mensajeria"]
    context = {
        "primary_nav": PRIMARY_NAV,
        "secondary_nav": SECONDARY_NAV,
        "active_key": "mensajeria",
        "item": item,
        "page_title": item.label,
        "section_template": _section_template("mensajeria"),
    }
    context.update(_mensajeria_context(request))
    context.update(panel_context)
    context["panel_template"] = panel_template
    return HttpResponse(render_to_string("base.html", context, request=request))


def plantilla_gallery(request):
    """Placeholder destination for the chooser's "Seleccionar plantilla" card.

    The template gallery gets built here later. HTMX gets the bare panel
    fragment; plain navigation gets it inside the full page shell.
    """
    template = "partials/mensajeria/panels/_galeria_plantillas.html"
    if _is_htmx(request):
        return HttpResponse(render_to_string(template, {}, request=request))
    return _mensajeria_page(request, template, {})


def _plantilla_editor_context(state=None, errors=None) -> dict:
    """Everything the Crear plantilla editor renders from: the option lists
    out of core.plantillas, the field values (defaults or a re-render of what
    was posted) and any validation errors."""
    return {
        "categories": plantillas.CATEGORIES,
        "sub_types": plantillas.SUB_TYPES,
        "languages": plantillas.LANGUAGES,
        "header_types": plantillas.HEADER_TYPES,
        "button_kinds": plantillas.BUTTON_KINDS,
        "teams": plantillas.team_options(),
        "form": state or plantillas.form_state(),
        "errors": errors or {},
        "body_max": plantillas.BODY_MAX,
        "header_text_max": plantillas.HEADER_TEXT_MAX,
        "footer_max": plantillas.FOOTER_MAX,
        "button_text_max": plantillas.BUTTON_TEXT_MAX,
    }


def plantilla_editor(request):
    """The Crear plantilla screen behind the chooser's "Empezar a crear" card.

    GET renders the two-column editor (form + live preview). POST validates
    through core.plantillas and, when clean, saves the template as Pendiente:
    HTMX gets the re-rendered Plantillas panel back (the region the editor
    was swapped into) while a plain POST redirects to the list. Errors
    re-render the editor with inline messages, keeping what was typed.
    """
    if request.method == "POST":
        state = plantillas.form_state(request.POST)
        errors = plantillas.validate(state, request.FILES)
        if not errors:
            template = MessageTemplate(**plantillas.model_kwargs(state, request.FILES))
            try:
                # The validator's exists() check can lose a race against a
                # concurrent save of the same (name, language); the unique
                # constraint backs it up, so answer with the same message.
                template.save()
            except IntegrityError:
                if template.header_media:
                    # FileField wrote the upload before the failed INSERT.
                    template.header_media.delete(save=False)
                errors["name"] = "Ya existe una plantilla con este nombre en este idioma."
            else:
                # TODO(meta): submit the new template to the Meta Cloud API
                # here once credentials exist (settings.META_ACCESS_TOKEN et
                # al.). Until then every template simply stays Pendiente.
                if _is_htmx(request):
                    return HttpResponse(
                        render_to_string(
                            "partials/mensajeria/panels/plantillas-whatsapp.html",
                            _plantillas_context(request),
                            request=request,
                        )
                    )
                return redirect(
                    reverse("section", args=["mensajeria"])
                    + "?view=plantillas-whatsapp"
                )
        context = _plantilla_editor_context(state=state, errors=errors)
    else:
        context = _plantilla_editor_context()

    template_name = "partials/mensajeria/panels/plantilla_editor.html"
    if _is_htmx(request):
        return HttpResponse(render_to_string(template_name, context, request=request))
    return _mensajeria_page(request, template_name, context)


# --- Mi calendario ---------------------------------------------------------


def _calendar_event_from_post(request, event=None):
    """Build or refresh a CalendarEvent from the event modal's POST.

    Returns ``(event, errors)`` -- errors is field -> Spanish message and
    nothing may be saved while it is non-empty. All datetimes go through
    core.calendario.parse_client_dt, so the modal's wall-clock strings land
    in the database as UTC.
    """
    data = request.POST
    errors = {}
    event = event or CalendarEvent()

    title = data.get("title", "").strip()
    if not title:
        errors["title"] = "Escribe un título."
    elif len(title) > 120:
        errors["title"] = "Máximo 120 caracteres."
    event.title = title

    event.description = data.get("description", "").strip()

    event_type = data.get("event_type", calendario.DEFAULT_EVENT_TYPE)
    if event_type in calendario.EVENT_TYPE_BY_KEY:
        event.event_type = event_type
    else:
        errors["event_type"] = "Elige un tipo de la lista."

    event.all_day = data.get("all_day") == "1"
    date_str = data.get("date", "").strip()
    try:
        if event.all_day:
            # Optional end_date makes multi-day all-day events expressible;
            # stored end stays exclusive (last day + 1).
            end_date = data.get("end_date", "").strip() or date_str
            event.start = calendario.parse_client_dt(f"{date_str}T00:00:00")
            event.end = calendario.parse_client_dt(f"{end_date}T00:00:00") + timedelta(
                days=1
            )
        else:
            start_time = data.get("start_time", "").strip()
            end_time = data.get("end_time", "").strip()
            event.start = calendario.parse_client_dt(f"{date_str}T{start_time}")
            event.end = calendario.parse_client_dt(f"{date_str}T{end_time}")
            # An event ending at midnight ends on the NEXT day.
            if event.end <= event.start and end_time == "00:00":
                event.end += timedelta(days=1)
    except (ValueError, OverflowError):
        errors["when"] = "Revisa la fecha y las horas."
    else:
        if event.end <= event.start:
            errors["when"] = (
                "La fecha de fin no puede ser anterior a la de inicio."
                if event.all_day
                else "La hora de fin debe ser posterior a la de inicio."
            )

    contact_id = data.get("contact", "").strip()
    if contact_id:
        try:
            event.contact = Client.objects.get(pk=int(contact_id))
        except (ValueError, Client.DoesNotExist):
            errors["contact"] = "Ese cliente no existe."
    else:
        event.contact = None

    assigned_id = data.get("assigned_to", "").strip()
    if assigned_id:
        try:
            event.assigned_to = get_user_model().objects.get(pk=int(assigned_id))
        except (ValueError, get_user_model().DoesNotExist):
            errors["assigned_to"] = "Ese usuario no existe."
    else:
        event.assigned_to = None

    reminder = data.get("reminder", "").strip()
    if not reminder:
        event.reminder_minutes_before = None
    elif reminder in calendario.REMINDER_KEYS:
        event.reminder_minutes_before = int(reminder)
    else:
        # Off-menu values would be stored but silently lost on the next
        # edit (the select can't show them), so reject them outright.
        errors["reminder"] = "Recordatorio inválido."

    return event, errors


def calendar_events(request):
    """JSON feed for the calendar grid: events overlapping [start, end).

    FullCalendar refetches this on every navigation, so only the visible
    range ever leaves the database -- never the whole table.
    """
    try:
        range_start = calendario.parse_client_dt(request.GET["start"])
        range_end = calendario.parse_client_dt(request.GET["end"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "Rango inválido."}, status=400)
    # FullCalendar's widest legitimate fetch is a padded month (~6 weeks);
    # cap the span so a hand-made range can't dump the whole table.
    if range_end <= range_start or range_end - range_start > timedelta(days=100):
        return JsonResponse({"error": "Rango inválido."}, status=400)

    events = CalendarEvent.objects.filter(
        start__lt=range_end, end__gt=range_start
    ).select_related("contact")
    return JsonResponse(
        [calendario.serialize_event(event) for event in events], safe=False
    )


def calendar_event_create(request):
    """Create an event from the modal. Answers JSON: the serialized event on
    success, field errors (400) otherwise."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event, errors = _calendar_event_from_post(request)
    if errors:
        return JsonResponse({"errors": errors}, status=400)
    if request.user.is_authenticated:
        event.created_by = request.user
    event.save()
    return JsonResponse({"ok": True, "event": calendario.serialize_event(event)})


def calendar_event_update(request, event_id: int):
    """Full edit of one event from the modal (same payload as create)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event = get_object_or_404(CalendarEvent, pk=event_id)
    event, errors = _calendar_event_from_post(request, event)
    if errors:
        return JsonResponse({"errors": errors}, status=400)
    event.save()
    return JsonResponse({"ok": True, "event": calendario.serialize_event(event)})


def calendar_event_move(request, event_id: int):
    """Persist a drag or resize: new start/end wall-clock strings (and
    whether the event landed in the all-day lane). The client updates
    optimistically and reverts if this answers anything but ok."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event = get_object_or_404(CalendarEvent, pk=event_id)
    try:
        start = calendario.parse_client_dt(request.POST["start"])
        end = calendario.parse_client_dt(request.POST["end"])
    except (KeyError, ValueError):
        return JsonResponse({"error": "Fechas inválidas."}, status=400)

    all_day = request.POST.get("all_day") == "1"
    if all_day:
        # A drag into the all-day lane arrives with arbitrary wall clocks
        # (FullCalendar may even drop the end); snap to midnights.
        start, end = calendario.normalize_all_day(start, end)
    elif end < start:
        return JsonResponse({"error": "Rango inválido."}, status=400)
    elif end == start:
        # A drop out of the all-day lane loses its end; give it an hour.
        end = start + timedelta(hours=1)

    event.start = start
    event.end = end
    event.all_day = all_day
    event.save(update_fields=["start", "end", "all_day", "updated_at"])
    return JsonResponse({"ok": True})


def calendar_event_delete(request, event_id: int):
    """Delete one event, from the modal's Eliminar action."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    get_object_or_404(CalendarEvent, pk=event_id).delete()
    return JsonResponse({"ok": True})


def calendar_prefs(request):
    """Persist the sidebar preferences (weekends toggle, slot duration) in
    the session and echo the validated values back."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    calendario.set_prefs(
        request.session,
        weekends=request.POST.get("weekends") == "1",
        slot=request.POST.get("slot", ""),
    )
    return JsonResponse({"ok": True, **calendario.get_prefs(request.session)})
