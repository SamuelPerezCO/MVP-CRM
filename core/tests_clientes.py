"""Tests for the Clientes CRUD: the rules in core.clientes, the four endpoints
behind the shared modal, and the search box on the table."""

from django.test import TestCase
from django.urls import reverse

from core import clientes
from core.models import Client, ClientList
from core.views import CLIENTS_PER_PAGE
from messaging.models import Conversation, Message


def make(first_name="Ana", last_name="Gil", phone="+573167687288", **extra):
    return Client.objects.create(
        first_name=first_name, last_name=last_name, phone=phone, **extra
    )


def payload(**overrides):
    data = {
        "first_name": "Camila",
        "last_name": "Rojas",
        "phone": "+57 316 768 7288",
        "country": "",
        "email": "camila@example.com",
        "channel": "whatsapp",
    }
    data.update(overrides)
    return data


class NormalizePhoneTests(TestCase):
    def test_formatting_is_stripped_to_plus_and_digits(self):
        for typed in ("+57 316 768 7288", "+57-316-768.7288", "(57) 316 7687288"):
            with self.subTest(typed):
                self.assertEqual(clientes.normalize_phone(typed), "+573167687288")

    def test_a_missing_plus_is_added(self):
        # Everything this CRM talks to is international; there is no local form.
        self.assertEqual(clientes.normalize_phone("573167687288"), "+573167687288")

    def test_a_string_without_digits_normalizes_to_empty(self):
        for junk in ("", "   ", "abc", "+"):
            with self.subTest(junk):
                self.assertEqual(clientes.normalize_phone(junk), "")

    def test_normalizing_matches_what_the_webhook_stores(self):
        # messaging.services._upsert_contact looks a contact up by an exact
        # phone match, so a number typed with spaces here has to come out the
        # same string the webhook would have written.
        self.assertEqual(
            clientes.normalize_phone("+57 316 768 7288"),
            clientes.normalize_phone("+573167687288"),
        )


class CountryFromPhoneTests(TestCase):
    def test_the_dialing_prefix_picks_the_country(self):
        self.assertEqual(clientes.country_from_phone("+573167687288"), "CO")
        self.assertEqual(clientes.country_from_phone("+525512345678"), "MX")
        self.assertEqual(clientes.country_from_phone("+34612345678"), "ES")

    def test_a_longer_prefix_wins_over_a_shorter_one(self):
        # +1809 is the Dominican Republic, not a US number starting with 809.
        self.assertEqual(clientes.country_from_phone("+18095551234"), "DO")

    def test_an_unknown_prefix_gives_nothing_rather_than_a_guess(self):
        self.assertEqual(clientes.country_from_phone("+9995551234"), "")


class ValidateTests(TestCase):
    def state(self, **overrides):
        return clientes.form_state(payload(**overrides))

    def test_a_complete_state_passes(self):
        self.assertEqual(clientes.validate(self.state()), {})

    def test_the_name_and_phone_are_required(self):
        self.assertIn("first_name", clientes.validate(self.state(first_name="  ")))
        self.assertIn("phone", clientes.validate(self.state(phone="")))

    def test_a_phone_that_is_too_short_or_too_long_is_rejected(self):
        for bad in ("+12", "+1234567890123456789"):
            with self.subTest(bad):
                self.assertIn("phone", clientes.validate(self.state(phone=bad)))

    def test_a_malformed_mail_is_rejected_but_a_blank_one_is_fine(self):
        self.assertIn("email", clientes.validate(self.state(email="camila@")))
        self.assertNotIn("email", clientes.validate(self.state(email="")))

    def test_a_country_or_channel_outside_the_lists_is_rejected(self):
        self.assertIn("country", clientes.validate(self.state(country="ZZ")))
        self.assertIn("channel", clientes.validate(self.state(channel="telegram")))

    def test_a_phone_already_in_use_is_rejected_by_name(self):
        make(first_name="Ana", last_name="Gil", phone="+573167687288")
        errors = clientes.validate(self.state(phone="+57 316 768 7288"))
        self.assertIn("Ana Gil", errors["phone"])

    def test_a_client_does_not_clash_with_themselves(self):
        ana = make(phone="+573167687288")
        state = self.state(phone="+573167687288")
        self.assertNotIn("phone", clientes.validate(state, ana))


class ApplyTests(TestCase):
    def test_the_country_is_derived_when_it_is_left_blank(self):
        client = clientes.apply(clientes.form_state(payload(country="")))
        self.assertEqual(client.country, "CO")

    def test_an_explicit_country_is_never_second_guessed(self):
        # A Colombian number on a client who lives in Spain is the agent's call.
        client = clientes.apply(clientes.form_state(payload(country="ES")))
        self.assertEqual(client.country, "ES")

    def test_the_stored_phone_is_the_normalized_one(self):
        client = clientes.apply(clientes.form_state(payload(phone="+57 316 768 7288")))
        self.assertEqual(client.phone, "+573167687288")


class ClienteCreateTests(TestCase):
    URL = reverse("cliente_create")

    def test_get_renders_the_empty_form(self):
        response = self.client.get(self.URL)
        self.assertContains(response, "Crear cliente")
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="phone"')
        self.assertContains(response, "Colombia (+57)")   # the country picker
        self.assertContains(response, "WhatsApp")         # the channel picker

    def test_post_creates_the_client(self):
        response = self.client.post(self.URL, payload())
        self.assertEqual(response.status_code, 200)
        client = Client.objects.get()
        self.assertEqual(client.full_name, "Camila Rojas")
        self.assertEqual(client.phone, "+573167687288")
        self.assertEqual(client.country, "CO")
        self.assertEqual(client.email, "camila@example.com")
        self.assertEqual(client.channel, "whatsapp")

    def test_a_successful_post_closes_the_modal_and_refreshes_the_table(self):
        html = self.client.post(self.URL, payload()).content.decode()
        self.assertIn("data-dialog-dismiss", html)
        # The table rides back out-of-band, so the new row appears behind the
        # modal as it closes.
        self.assertIn('id="client-table" hx-swap-oob="innerHTML"', html)
        self.assertIn("Camila Rojas", html)

    def test_a_rejected_post_returns_the_form_with_errors_and_the_values(self):
        html = self.client.post(self.URL, payload(first_name="", email="nope")).content.decode()
        self.assertEqual(Client.objects.count(), 0)
        self.assertNotIn("data-dialog-dismiss", html)   # the dialog stays open
        self.assertIn("El nombre es obligatorio.", html)
        self.assertIn("Escribe un correo válido.", html)
        self.assertIn("ffield--error", html)
        self.assertIn('value="Rojas"', html)            # nothing typed is lost

    def test_a_duplicate_phone_is_refused(self):
        make(first_name="Ana", last_name="Gil", phone="+573167687288")
        html = self.client.post(self.URL, payload()).content.decode()
        self.assertEqual(Client.objects.count(), 1)
        self.assertIn("Ana Gil", html)

    def test_the_toolbar_button_opens_the_modal_from_this_route(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn(f'hx-get="{self.URL}"', html)
        self.assertIn('data-dialog-open="client-modal"', html)
        self.assertIn('id="client-modal-body"', html)


class ClienteUpdateTests(TestCase):
    def setUp(self):
        self.client_row = make(
            first_name="Ana", last_name="Gil", phone="+573167687288",
            country="CO", email="ana@example.com", channel="whatsapp",
        )
        self.url = reverse("cliente_update", args=[self.client_row.pk])

    def test_get_prefills_the_form(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("Editar cliente", html)
        self.assertIn('value="Ana"', html)
        self.assertIn('value="+573167687288"', html)
        # The country picker opens on the client's own country, not the top
        # of the list -- whitespace-insensitive, the markup wraps.
        picked = html.split('name="country"', 1)[1].split("</select>", 1)[0]
        self.assertIn("selected", picked.split('value="CO"', 1)[1].split(">", 1)[0])

    def test_post_updates_the_row(self):
        self.client.post(self.url, payload(first_name="Ana", last_name="Gil Mora",
                                           phone="+573167687288"))
        self.client_row.refresh_from_db()
        self.assertEqual(self.client_row.full_name, "Ana Gil Mora")

    def test_saving_without_touching_the_phone_is_not_a_duplicate(self):
        response = self.client.post(
            self.url, payload(first_name="Ana", phone="+57 316 768 7288")
        )
        self.assertContains(response, "data-dialog-dismiss")

    def test_the_edit_dialog_offers_delete_and_the_create_one_does_not(self):
        self.assertContains(self.client.get(self.url), "Eliminar")
        self.assertNotContains(self.client.get(reverse("cliente_create")), "Eliminar")

    def test_an_unknown_client_is_404(self):
        self.assertEqual(
            self.client.get(reverse("cliente_update", args=[999999])).status_code, 404
        )


class ClienteDetailTests(TestCase):
    def setUp(self):
        self.client_row = make(channel="whatsapp", email="ana@example.com")
        self.url = reverse("cliente_detail", args=[self.client_row.pk])

    def test_it_shows_what_the_row_has_no_column_for(self):
        Conversation.objects.create(contact=self.client_row, channel="whatsapp")
        ClientList.objects.create(name="VIP").clients.add(self.client_row)

        html = self.client.get(self.url).content.decode()
        self.assertIn("Ana Gil", html)
        self.assertIn("Cliente desde", html)
        self.assertIn("VIP", html)
        self.assertIn("WhatsApp", html)

    def test_a_client_with_no_history_says_so_instead_of_showing_blanks(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("Todavía ninguna.", html)
        self.assertIn("En ninguna lista.", html)

    def test_it_offers_the_edit_form_without_a_second_dialog(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn(reverse("cliente_update", args=[self.client_row.pk]), html)
        self.assertIn('hx-target="#client-modal-body"', html)

    def test_the_eye_button_on_the_row_points_here(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn(f'hx-get="{self.url}"', html)


class ClienteDeleteTests(TestCase):
    def setUp(self):
        self.client_row = make()
        self.url = reverse("cliente_delete", args=[self.client_row.pk])

    def test_get_asks_before_deleting(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("Eliminar a Ana Gil", html)
        self.assertEqual(Client.objects.count(), 1)

    def test_the_confirmation_counts_the_history_the_delete_takes_with_it(self):
        # Conversation.contact cascades, so this is not a hypothetical warning.
        for _ in range(2):
            Conversation.objects.create(contact=self.client_row, channel="whatsapp")
        html = self.client.get(self.url).content.decode()
        self.assertIn("2 conversaciones", html)

    def test_the_singular_reads_properly(self):
        # "sus 1 conversación" is what a naive pluralize filter produces.
        Conversation.objects.create(contact=self.client_row, channel="whatsapp")
        html = self.client.get(self.url).content.decode()
        self.assertIn("Se eliminará también su conversación", html)
        self.assertNotIn("sus 1", html)

    def test_a_client_with_no_threads_gets_the_plain_warning(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("de forma permanente", html)
        self.assertNotIn("historial de mensajes", html)

    def test_post_deletes_the_client_and_their_threads(self):
        conversation = Conversation.objects.create(
            contact=self.client_row, channel="whatsapp"
        )
        Message.objects.create(
            conversation=conversation, direction="out", body="hola"
        )
        response = self.client.post(self.url)
        self.assertContains(response, "data-dialog-dismiss")
        self.assertEqual(Client.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    def test_the_trash_button_on_the_row_points_here(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn(f'hx-get="{self.url}"', html)
        self.assertIn('aria-label="Eliminar Ana Gil"', html)


class ClientSearchTests(TestCase):
    def setUp(self):
        make(first_name="Ana", last_name="Gil", phone="+573167687288",
             email="ana@example.com")
        make(first_name="Bruno", last_name="Paz", phone="+525512345678",
             email="bruno@correo.mx")

    def rows(self, **params):
        return self.client.get(reverse("clientes_table"), params).content.decode()

    def test_searching_by_name_narrows_the_table(self):
        html = self.rows(q="ana")
        self.assertIn("Ana Gil", html)
        self.assertNotIn("Bruno Paz", html)

    def test_searching_by_mail_works_too(self):
        self.assertIn("Bruno Paz", self.rows(q="correo.mx"))

    def test_a_phone_search_ignores_the_formatting_around_it(self):
        # The agent reads "+57 316 768 7288" off a screen and types it back.
        self.assertIn("Ana Gil", self.rows(q="316 768"))
        self.assertNotIn("Bruno Paz", self.rows(q="316 768"))

    def test_no_matches_says_so_rather_than_claiming_there_are_no_clients(self):
        html = self.rows(q="zzz")
        self.assertIn("Sin resultados", html)
        self.assertNotIn("Aún no tienes clientes", html)

    def test_an_empty_search_shows_everyone_again(self):
        html = self.rows(q="")
        self.assertIn("Ana Gil", html)
        self.assertIn("Bruno Paz", html)

    def test_the_toolbar_box_swaps_only_the_table(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn(f'hx-get="{reverse("clientes_table")}"', html)
        self.assertIn('hx-target="#client-table"', html)
        self.assertIn('id="client-search"', html)

    def test_the_pager_keeps_the_search(self):
        Client.objects.bulk_create(
            Client(first_name=f"Ana {n:03d}", phone=f"+5731676872{n:02d}")
            for n in range(CLIENTS_PER_PAGE)
        )
        html = self.rows(q="ana")
        self.assertIn("pager__status", html)
        self.assertIn('hx-include="#client-search"', html)


class ClientTableContextTests(TestCase):
    """The hidden page/search inputs the CRUD buttons hx-include."""

    def test_the_current_page_travels_with_every_crud_request(self):
        Client.objects.bulk_create(
            Client(first_name=f"C{n:03d}", phone=f"+5731676872{n:02d}")
            for n in range(CLIENTS_PER_PAGE + 5)
        )
        html = self.client.get(reverse("clientes_table"), {"page": 2}).content.decode()
        self.assertIn('id="client-page" name="page" value="2"', html)
        self.assertIn('hx-include="#client-search, #client-page"', html)

    def test_a_save_re_renders_the_page_the_agent_was_on(self):
        Client.objects.bulk_create(
            Client(first_name=f"C{n:03d}", phone=f"+5731676872{n:02d}")
            for n in range(CLIENTS_PER_PAGE + 5)
        )
        # The dialog posts the page back, so the table doesn't jump to 1.
        html = self.client.post(
            reverse("cliente_create"), payload(first_name="Zulema", page="2")
        ).content.decode()
        self.assertIn("Página 2 de 2", html)
