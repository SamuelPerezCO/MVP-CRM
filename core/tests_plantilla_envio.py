"""Tests for the Enviar plantilla dialog: the screen half of writing first to
a client.

The service layer (messaging.tests_envio_plantillas) owns the rules; these
pin the wiring an agent actually meets -- the button on a client row, the
button that replaces the closed composer, the price on the form, and the fact
that a refusal comes back as a readable line instead of a silent no-op.

How to read this file if Django's test tooling is new to you:

* Run it with ``python manage.py test core.tests_plantilla_envio``. The
  runner collects every ``TestCase`` subclass here and calls each ``test_*``
  method; a method that raises (a failing ``assert*`` raises) is a failure.
* ``django.test.TestCase`` runs each test method inside its own database
  transaction and rolls it back afterwards, so rows never leak between
  tests, ``setUp`` always starts from empty tables, and nothing has to clean
  up after itself.
* ``self.client`` is the one thing ``TestCase`` hands every test for free:
  Django's *test client*, a fake browser that calls the URL resolver and the
  view stack in process and returns the response object, with no server and
  no network. It is unrelated to ``core.models.Client``, the CRM's own
  customer model -- which is why the model rows below are named
  ``self.contact`` and built by ``make_client``.
* ``reverse("route_name", args=[...])`` builds a URL from the ``name=``
  given in core/urls.py, so these tests keep passing if a path is renamed.
  The same names appear in the templates as ``{% url %}``.
* ``response.content`` is bytes; ``.decode()`` turns it into the HTML string
  the assertions below search with ``assertIn``. These are substring checks
  against real rendered markup, so a Spanish sentence, an attribute or a URL
  is asserted exactly as the template prints it.
* Nothing is sent for real. ``config/settings.py`` forces
  ``MESSAGING_PROVIDER = 'fake'`` while the test runner is active, so the
  send in ``SendTests`` reaches the in-process FakeProvider, which invents a
  message id and logs instead of calling WhatsApp.
* No test logs in, although the whole app sits behind
  ``core.middleware.LoginRequiredMiddleware``: that middleware lets every
  request through when ``settings.TESTING`` is true, which it is under
  ``manage.py test``.
* ``override_settings`` swaps values into ``django.conf.settings`` for the
  class, method or ``with`` block it decorates and restores them afterwards.
  It is what pins the prices below: ``messaging.pricing`` re-reads settings
  on every call and caches nothing.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Client, MessageTemplate
from messaging.models import Conversation, Message

# Extra keyword arguments handed to the test client, the convention this test
# suite uses to stand for the "HX-Request: true" header htmx sends. Note that
# the test client merges these into the WSGI environ as they are written, and
# ``request.headers`` only exposes environ keys prefixed ``HTTP_``, so
# ``core.views._is_htmx`` does not actually see it. Nothing here depends on
# that: none of the three views these tests call (plantilla_send_form,
# plantilla_send, inbox_thread) branches on it -- each always answers with
# the same HTML fragment.
HTMX = {"HX-Request": "true"}

# A price list in the shape MESSAGING_TEMPLATE_RATES takes: JSON, country ->
# category -> price. Every client in this file is Colombian (+57, country
# "CO"), so a single CO row is enough. The prices are strings for the same
# reason pricing.DEFAULT_RATES uses Decimal("..."): a JSON number would be
# parsed as a float first, and 0.0125 has no exact binary form.
RATES = json.dumps(
    {"CO": {"marketing": "0.0125", "utility": "0.0022", "authentication": "0.0077"}}
)


# Insert one CRM client and return it. ``objects.create()`` builds the
# instance, INSERTs it and hands it back with its primary key filled in.
# ``extra.pop(key, default)`` is the pattern that lets a caller override any
# of these fields while the rest keep their defaults; whatever is left in
# ``extra`` (``channel`` in ClientRowTests) goes straight to the model.
# first_name + last_name are what Client.full_name joins into the heading the
# tests look for, and phone + country are what the RATES table above prices.
def make_client(**extra):
    return Client.objects.create(
        first_name=extra.pop("first_name", "Camila"),
        last_name=extra.pop("last_name", "Ríos"),
        phone=extra.pop("phone", "+573000000001"),
        country=extra.pop("country", "CO"),
        **extra,
    )


# Insert one plantilla, sendable by default: active (the account's own
# toggle) and "aceptada" (WhatsApp's verdict), which is what puts it in
# services.sendable_templates() and so in the dialog's picker.
# body_sample_values is the JSON list of samples, element i pairing with
# {{i+1}}, so "Camila" is the fallback shown for {{1}} and sent when the
# field is left blank. The model is unique on (name, language), so a test
# that creates a second plantilla has to pass a different name.
def make_template(**extra):
    return MessageTemplate.objects.create(
        name=extra.pop("name", "saludo_inicial"),
        body=extra.pop("body", "Hola {{1}}, gracias por escribirnos."),
        body_sample_values=extra.pop("body_sample_values", ["Camila"]),
        category=extra.pop("category", "marketing"),
        status=extra.pop("status", "aceptada"),
        **extra,
    )


# What the dialog shows before anything is sent: core.views.plantilla_send_form
# rendering partials/plantillas/send_form.html.
# Pinned for every test in the class, so the amounts asserted below do not
# depend on the developer's own .env.
@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_CURRENCY="USD")
class SendFormTests(TestCase):
    # Runs before each test method, inside that method's own transaction: one
    # client and one sendable plantilla, and deliberately no Conversation --
    # this is the new client the feature exists for.
    def setUp(self):
        self.contact = make_client()
        self.template = make_template()

    # GET the form the way the dialog does. The second positional argument of
    # the test client's get() is the query string, so ``self.get()`` is the
    # opener button's bare request and ``self.get(template=<pk>)`` is exactly
    # what the picker's hx-get sends when the selection changes.
    def get(self, **params):
        return self.client.get(
            reverse("plantilla_send_form", args=[self.contact.pk]), params, **HTMX
        )

    # The whole form in one pass. Nothing was asked for, so
    # _send_form_context picks the first sendable plantilla: the heading is
    # built from client.full_name, "saludo_inicial" is the picker's option,
    # the preview bubble shows the body with {{1}} already swapped for the
    # sample, and the price is the CO marketing rate above printed with
    # floatformat:4 next to quote.currency.
    def test_renders_the_picker_the_preview_and_the_price(self):
        html = self.get().content.decode()
        self.assertIn("Enviar plantilla a Camila Ríos", html)
        self.assertIn("saludo_inicial", html)
        self.assertIn("Hola Camila, gracias por escribirnos.", html)
        self.assertIn("0.0125", html)
        self.assertIn("USD", html)

    # This client has no conversation at all, so window_open is False and the
    # form renders the closed-window half of its notice -- the two sentences
    # that say why only a plantilla will arrive and that it is billed.
    def test_says_the_window_is_closed_and_that_the_send_is_charged(self):
        html = self.get().content.decode()
        self.assertIn("no ha escrito en las últimas 24 horas", html)
        self.assertIn("cobra cada envío", html)

    # The body has one {{1}}, so the form renders one input, named var_1 --
    # the name core.views._posted_values parses the number back out of. The
    # sample reaches the page three times (the input's data-sample, the
    # label's "ejemplo" hint and the preview), so one assertion covers it.
    def test_offers_one_input_per_variable_with_its_sample(self):
        html = self.get().content.decode()
        self.assertIn('name="var_1"', html)
        self.assertIn("Camila", html)

    # Why the picker re-fetches the whole form instead of just swapping a
    # name: categories are priced apart. A second plantilla (a different
    # name, as the unique constraint requires) in the utility category, asked
    # for the way the picker asks, and the form comes back at the utility
    # rate. The assertNotIn is the meaningful half -- the marketing price is
    # gone, and no other number on the page happens to be 0.0125 (this
    # month's spend line prints 0.0000, nothing having been sent).
    def test_picking_another_plantilla_reprices_the_send(self):
        utility = make_template(name="pedido_en_camino", category="utility")
        html = self.get(template=utility.pk).content.decode()
        self.assertIn("0.0022", html)
        self.assertNotIn("0.0125", html)

    # The property the dialog depends on: _send_form_context looks for a
    # thread with services.find_open_conversation, which never creates one,
    # so opening the dialog on ten clients leaves no empty threads in the
    # Inbox claiming they were written to.
    def test_does_not_create_a_conversation_just_by_opening(self):
        self.get()
        self.assertEqual(Conversation.objects.count(), 0)

    # sendable_templates keeps pendientes on the list (this MVP has no
    # approval pipeline), so the form badges them instead of hiding them.
    # Deleting the aceptada plantilla from setUp first leaves the pendiente
    # one as the only option, and so the one the form selects.
    def test_a_pending_plantilla_carries_its_warning(self):
        MessageTemplate.objects.all().delete()
        make_template(name="pendiente_x", status="pendiente")
        self.assertIn("pendiente de aprobación", self.get().content.decode())

    # With nothing to offer, the form renders its empty state rather than a
    # picker with no options. The second assertion pins the way out: the
    # link's {% url 'section' 'mensajeria' %} builds the same path reverse()
    # builds here, so a renamed route breaks the test, not the agent.
    def test_without_plantillas_it_points_at_where_to_create_one(self):
        MessageTemplate.objects.all().delete()
        html = self.get().content.decode()
        self.assertIn("Todavía no hay plantillas para enviar", html)
        self.assertIn(reverse("section", args=["mensajeria"]), html)

    # The other half of the notice. A conversation whose last_inbound_at is
    # now means the customer wrote a moment ago (the Conversation defaults
    # fill in channel "whatsapp" and status "open", which is what makes
    # find_open_conversation return it). The assertTrue documents that
    # precondition; "gratis" is the word only the open-window branch prints.
    def test_an_open_window_says_free_text_is_available_too(self):
        conversation = Conversation.objects.create(
            contact=self.contact, last_inbound_at=timezone.now()
        )
        self.assertTrue(conversation.is_within_24h_window)
        self.assertIn("gratis", self.get().content.decode())


# The POST: core.views.plantilla_send, which sends through
# messaging.services.send_template and answers with either send_sent.html or
# the form again carrying an error line.
@override_settings(MESSAGING_TEMPLATE_RATES=RATES, MESSAGING_CURRENCY="USD")
class SendTests(TestCase):
    def setUp(self):
        self.contact = make_client()
        self.template = make_template()

    # POST the form. setdefault only fills a key that is absent, so a test
    # can override the plantilla (or blank it) and still use this helper.
    # The test client sends ``data`` as the request body and needs no CSRF
    # token: it runs with CSRF checks disabled unless asked otherwise.
    def post(self, **data):
        data.setdefault("template", self.template.pk)
        return self.client.post(
            reverse("plantilla_send", args=[self.contact.pk]), data, **HTMX
        )

    # The happy path, both halves: what was stored and what the agent sees.
    # var_1 is the input name, so "Andrés" replaces {{1}} in the body that
    # actually went out. Message.objects.get() with no arguments returns the
    # single row and raises if there are none or several, so it doubles as a
    # count check. The billed amount is the CO marketing rate, frozen onto
    # the row by send_template. The last two assertions are send_sent.html:
    # its heading, and the cost it repeats back.
    def test_sends_the_plantilla_and_reports_the_cost(self):
        response = self.post(var_1="Andrés")
        html = response.content.decode()

        message = Message.objects.get()
        self.assertEqual(message.body, "Hola Andrés, gracias por escribirnos.")
        self.assertEqual(message.template, self.template)
        self.assertEqual(message.billed_amount, Decimal("0.0125"))

        self.assertIn("Plantilla enviada", html)
        self.assertIn("0.0125", html)

    # The point of keying the whole flow by client rather than conversation:
    # this contact has no thread, so the view opens one (channel "whatsapp",
    # the Conversation default and the only channel plantillas exist for) and
    # the message lands in it. objects.get(contact=...) again asserts there
    # is exactly one.
    def test_starts_the_conversation_for_a_client_who_had_none(self):
        self.post()
        conversation = Conversation.objects.get(contact=self.contact)
        self.assertEqual(conversation.channel, "whatsapp")
        self.assertEqual(conversation.messages.count(), 1)

    def test_tells_the_open_inbox_thread_to_refresh(self):
        # The thread listens for this event, so the message the agent just
        # paid for appears at once instead of on the next five-second poll.
        # Subscripting a response reads a response *header*: the view sets
        # HX-Trigger, htmx turns it into a DOM event of that name, and
        # chat_thread.html's hx-trigger="... plantilla-enviada from:body"
        # picks it up.
        self.assertEqual(self.post()["HX-Trigger"], "plantilla-enviada")

    # send_sent.html's "Ver la conversación" link. Only the query string is
    # asserted because that is the part carrying the new thread's id --
    # core.views._inbox_context reads ?chat= to open it.
    def test_offers_the_way_into_the_conversation_it_started(self):
        html = self.post().content.decode()
        conversation = Conversation.objects.get()
        self.assertIn(f"?chat={conversation.pk}", html)

    # template="" overrides the setdefault above, so the POST arrives with
    # the field empty -- what a form submitted before anything was picked
    # would send. _selected_template answers None and the view re-renders the
    # form with the error instead of guessing a plantilla.
    def test_without_a_plantilla_it_asks_for_one_and_sends_nothing(self):
        html = self.post(template="").content.decode()
        self.assertIn("Elige una plantilla", html)
        self.assertEqual(Message.objects.count(), 0)

    # A real pk, but a rechazada plantilla, which sendable_templates()
    # excludes: _selected_template only accepts ids the picker offered, so a
    # hand-crafted POST gets the same answer as an empty field. The view does
    # not distinguish the two on purpose -- both mean "nothing valid was
    # picked" -- which is why the same Spanish line is asserted here.
    def test_a_plantilla_outside_the_picker_is_not_sent(self):
        rejected = make_template(name="rechazada_x", status="rechazada")
        html = self.post(template=rejected.pk).content.decode()
        self.assertIn("Elige una plantilla", html)
        self.assertEqual(Message.objects.count(), 0)

    # A ceiling below the price of a single send, so the very first one is
    # refused: send_template raises BudgetExceeded before writing any row,
    # and the view puts str(exc) -- a Spanish sentence naming the ceiling --
    # on the form.
    @override_settings(MESSAGING_MONTHLY_BUDGET="0.01")
    def test_the_budget_refusal_is_shown_on_the_form(self):
        html = self.post().content.decode()
        self.assertIn("presupuesto mensual", html)
        self.assertEqual(Message.objects.count(), 0)
        # Still the form, so the agent can fix it and retry.
        self.assertIn('name="var_1"', html)

    @override_settings(MESSAGING_MONTHLY_BUDGET="0.01")
    def test_a_refused_send_leaves_no_empty_conversation_behind(self):
        # An empty thread in the Inbox would claim this client was written
        # to when nothing went out.
        # The view opens the thread before calling send_template (a send is
        # about to happen) and deletes it again when the refusal comes back,
        # which is what this counts.
        self.post()
        self.assertEqual(Conversation.objects.count(), 0)

    # The other side of that cleanup: the view only deletes a thread it
    # opened itself. Here one already existed, find_open_conversation returns
    # it, and the refusal leaves it exactly where it was.
    @override_settings(MESSAGING_MONTHLY_BUDGET="0.01")
    def test_a_refusal_does_not_touch_a_thread_that_already_existed(self):
        conversation = Conversation.objects.create(contact=self.contact)
        self.post()
        self.assertTrue(Conversation.objects.filter(pk=conversation.pk).exists())

    # Visiting the URL must never spend money: the view answers 405 (Method
    # Not Allowed) to anything but POST, before it looks at anything else.
    def test_get_is_not_a_way_to_send(self):
        response = self.client.get(
            reverse("plantilla_send", args=[self.contact.pk]), **HTMX
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Message.objects.count(), 0)


class ClientRowTests(TestCase):
    """The CRM entry point: a send button on the row of anyone WhatsApp can
    reach, which is how a client typed in by hand ever gets a first message.

    No ``override_settings`` here: nothing is sent or priced, these are
    assertions about the markup the Clientes panel renders.
    """

    # The whole CRM screen as a browser would get it. Without the HTMX dict
    # (and it would make no difference with it, see the module docstring) the
    # section view answers with the full page, section fragment included, so
    # the substring assertions below still find the table. ?view=clientes
    # picks the Clientes panel of the secondary nav -- also the default, but
    # written out so the test does not depend on that default.
    def crm_html(self):
        url = reverse("section", args=["crm"]) + "?view=clientes"
        return self.client.get(url).content.decode()

    # channel="" is the client somebody typed into the CRM: no channel, so
    # Client.has_whatsapp is False, but can_receive_template is True because
    # there is a number to try. Two assertions: the row's hx-get points at
    # this client's form, and the icon-only button carries a name for screen
    # readers.
    def test_a_client_added_by_hand_can_be_written_to(self):
        contact = make_client(channel="")
        html = self.crm_html()
        self.assertIn(reverse("plantilla_send_form", args=[contact.pk]), html)
        self.assertIn("Enviar plantilla a Camila Ríos", html)

    def test_a_whatsapp_client_can_be_written_to(self):
        contact = make_client(channel="whatsapp")
        self.assertIn(reverse("plantilla_send_form", args=[contact.pk]), self.crm_html())

    def test_a_client_from_another_channel_is_not_offered_it(self):
        # Plantillas are a WhatsApp mechanism; offering the button on an
        # Instagram row would promise something that cannot be delivered.
        # can_receive_template accepts only "" and "whatsapp", so the
        # {% if %} around the button in client_table.html renders nothing and
        # this client's URL never appears on the page.
        contact = make_client(channel="instagram")
        self.assertNotIn(
            reverse("plantilla_send_form", args=[contact.pk]), self.crm_html()
        )

    # The <dialog> is included once by the panel, below the table, not inside
    # the row loop: every row's button opens that one dialog and loads its
    # own body into it. Two clients means two rows; counting the id proves
    # there is still a single dialog (duplicate ids would also break the
    # hx-target lookups, which resolve by id across the whole document).
    def test_the_dialog_is_mounted_once_for_the_whole_table(self):
        make_client()
        make_client(phone="+573000000002")
        html = self.crm_html()
        self.assertEqual(html.count('id="plantilla-send-dialog"'), 1)

    # Campañas is a second section that mounts the same account nav and the
    # same panels (core.views.SECTION_CONTEXT maps both "crm" and "campanas"
    # to _crm_context), so the send button has to work there too rather than
    # only on the screen it was built for.
    def test_campanas_mounts_the_same_panel(self):
        contact = make_client()
        html = self.client.get(
            reverse("section", args=["campanas"]) + "?view=clientes"
        ).content.decode()
        self.assertIn(reverse("plantilla_send_form", args=[contact.pk]), html)


class InboxComposerTests(TestCase):
    """The Inbox entry point: the closed composer offers the one send that
    still works."""

    def setUp(self):
        self.contact = make_client()

    # Open one thread in the Inbox and return the page. The Conversation
    # defaults matter: channel "whatsapp", status "open" and -- since nothing
    # sets last_inbound_at -- a closed 24-hour window, which is the state
    # this class is about. ``**conversation_kwargs`` is how a test changes
    # one of those. ?chat=<pk> is what core.views._inbox_context reads to
    # render this thread's chat panel into the page.
    def chat_html(self, **conversation_kwargs):
        conversation = Conversation.objects.create(
            contact=self.contact, **conversation_kwargs
        )
        url = reverse("section", args=["inbox"]) + f"?chat={conversation.pk}"
        return self.client.get(url).content.decode()

    # The closed-window branch of chat_thread.html: the button's label, its
    # hx-get (keyed by the *client*, not by this conversation, because the
    # same form serves clients who have no thread at all), and the clause
    # that only a WhatsApp thread adds to the notice.
    def test_a_closed_window_offers_the_plantilla_send(self):
        html = self.chat_html()
        self.assertIn("Enviar plantilla", html)
        self.assertIn(reverse("plantilla_send_form", args=[self.contact.pk]), html)
        self.assertIn("se cobra por mensaje", html)

    # With the customer having written a moment ago, window_open is True and
    # the other branch renders: the ordinary text composer, whose placeholder
    # is asserted, and no plantilla button anywhere -- a free reply should not
    # be pushed towards a billed one.
    def test_an_open_window_keeps_the_free_text_composer(self):
        html = self.chat_html(last_inbound_at=timezone.now())
        self.assertIn("Escribe un mensaje", html)
        self.assertNotIn("Enviar plantilla", html)

    # An Instagram DM thread: the window is closed here too, so the notice
    # renders, but both the cost clause and the button are guarded on
    # channel == "whatsapp" and neither appears.
    def test_another_channel_keeps_the_plain_notice(self):
        html = self.chat_html(channel="instagram-dm")
        self.assertIn("La ventana de 24 horas está cerrada", html)
        self.assertNotIn("Enviar plantilla", html)

    # The receiving half of the HX-Trigger asserted in SendTests: this exact
    # string is the second entry of #chat-messages' hx-trigger list, which is
    # what makes the thread re-fetch itself when the dialog reports a send.
    def test_the_thread_listens_for_the_send(self):
        self.assertIn("plantilla-enviada from:body", self.chat_html())

    # What a billed send looks like once it is history. The Message row is
    # written by hand rather than sent, so no provider and no pricing are
    # involved -- this is only about chat_messages.html reading the columns
    # send_template fills. inbox_thread is the endpoint the thread's own poll
    # calls; it answers with the message list alone, where the bubble carries
    # a pill naming the plantilla and the amount (floatformat:4).
    def test_a_template_send_is_labelled_with_its_cost_in_the_thread(self):
        conversation = Conversation.objects.create(contact=self.contact)
        Message.objects.create(
            conversation=conversation,
            direction=Message.OUTBOUND,
            body="Hola Camila",
            template=make_template(),
            billed_amount=Decimal("0.0125"),
            billed_currency="USD",
            billed_category="marketing",
        )
        html = self.client.get(
            reverse("inbox_thread", args=[conversation.pk]), **HTMX
        ).content.decode()
        self.assertIn("saludo_inicial", html)
        self.assertIn("0.0125", html)
