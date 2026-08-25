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

from django.http import Http404, HttpResponse
from django.template import TemplateDoesNotExist
from django.template.loader import get_template, render_to_string

from django.core.paginator import Paginator

from . import crm, embudos, inbox
from .models import Client
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


def _inbox_context(request) -> dict:
    """Extra context for the Inbox screen.

    ``?filter=`` selects the active row in the nav panel. An unknown value falls
    back to the default rather than 404-ing, so a stale bookmark still opens.
    """
    filter_key = request.GET.get("filter", inbox.DEFAULT_FILTER)
    if filter_key not in inbox.FILTER_BY_KEY:
        filter_key = inbox.DEFAULT_FILTER

    return {
        "filter_groups": inbox.FILTER_GROUPS,
        "active_filter": filter_key,
        "counts": inbox.get_counts(),
        "conversations": inbox.get_conversations(filter_key),
    }


#: How many clients per page in the CRM table.
CLIENTS_PER_PAGE = 25


def _clientes_context(request) -> dict:
    """Paginated client list for the CRM's Clientes table."""
    page = Paginator(Client.objects.all(), CLIENTS_PER_PAGE).get_page(
        request.GET.get("page")
    )
    return {"clients": page, "page_obj": page}


#: CRM view key -> callable(request) -> dict. Panels without an entry need no data.
PANEL_CONTEXT = {
    "clientes": _clientes_context,
}


def _crm_context(request) -> dict:
    """Extra context for the CRM screen.

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


#: section key -> callable(request) -> dict of extra template context.
SECTION_CONTEXT = {
    "inbox": _inbox_context,
    "crm": _crm_context,
    "embudos": _embudos_context,
}


# --- Views -----------------------------------------------------------------


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

    Targeted by the nav panel's HTMX requests so picking a filter re-renders
    only that column. When a Conversation model lands, this view keeps the same
    shape -- ``get_conversations`` starts returning rows and the template that
    currently draws the empty state starts drawing the list instead.
    """
    if filter_key not in inbox.FILTER_BY_KEY:
        raise Http404(f"Unknown filter: {filter_key!r}")

    return HttpResponse(
        render_to_string(
            "partials/inbox/conversation_list.html",
            {
                "active_filter": filter_key,
                "conversations": inbox.get_conversations(filter_key),
            },
            request=request,
        )
    )


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
