"""Tests for pulling the WABA's templates back from Meta.

Two things only Meta knows: which plantillas may actually send (APPROVED
only) and the category it has assigned, which is what a send is billed at.
The Graph API itself is mocked -- there are no credentials in tests -- but
the payloads are Meta's documented shape.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import MessageTemplate

from . import services
from .providers.meta import MetaProvider


def meta_row(**overrides):
    row = {
        "id": "1689556908129832",
        "name": "saludo_inicial",
        "language": "es",
        "status": "APPROVED",
        "category": "MARKETING",
    }
    row.update(overrides)
    return row


def local(**overrides):
    fields = {
        "name": "saludo_inicial",
        "language": "es",
        "body": "Hola {{1}}",
        "body_sample_values": ["Camila"],
        "category": "marketing",
        "status": "pendiente",
    }
    fields.update(overrides)
    return MessageTemplate.objects.create(**fields)


def sync_with(rows):
    provider = Mock()
    provider.name = "meta"
    provider.fetch_templates.return_value = rows
    with patch.object(services, "get_provider", return_value=provider):
        return services.sync_templates()


class SyncTests(TestCase):
    def test_metas_approval_lands_on_the_local_row(self):
        template = local(status="pendiente")
        report = sync_with([meta_row(status="APPROVED")])

        template.refresh_from_db()
        self.assertEqual(template.meta_status, "APPROVED")
        self.assertEqual(template.status, "aceptada")
        self.assertEqual(template.meta_template_id, "1689556908129832")
        self.assertIsNotNone(template.meta_synced_at)
        self.assertEqual(report["updated"], 1)

    def test_a_status_the_crm_has_no_word_for_reads_as_pendiente(self):
        # PAUSED, DISABLED, IN_APPEAL... all mean "not usable yet" to the UI,
        # but the raw verdict is kept so the send picker can tell them apart.
        for meta_status in ("PAUSED", "DISABLED", "IN_APPEAL", "LIMIT_EXCEEDED"):
            with self.subTest(meta_status=meta_status):
                MessageTemplate.objects.all().delete()
                template = local()
                sync_with([meta_row(status=meta_status)])
                template.refresh_from_db()
                self.assertEqual(template.status, "pendiente")
                self.assertEqual(template.meta_status, meta_status)

    def test_a_rejection_is_carried_over(self):
        template = local(status="aceptada")
        sync_with([meta_row(status="REJECTED")])
        template.refresh_from_db()
        self.assertEqual(template.status, "rechazada")

    def test_a_recategorisation_changes_the_category_and_is_reported(self):
        # The one that moves money: Meta bills at its own category.
        template = local(category="utility")
        report = sync_with([meta_row(category="MARKETING")])

        template.refresh_from_db()
        self.assertEqual(template.category, "marketing")
        self.assertEqual(
            report["recategorised"],
            [{"name": "saludo_inicial", "from": "utility", "to": "marketing"}],
        )

    def test_a_category_the_crm_cannot_store_is_left_alone(self):
        template = local(category="utility")
        report = sync_with([meta_row(category="FREE_SERVICE")])

        template.refresh_from_db()
        self.assertEqual(template.category, "utility")
        self.assertEqual(report["recategorised"], [])

    def test_a_template_only_meta_knows_is_imported(self):
        report = sync_with([meta_row(name="creada_en_manager")])

        imported = MessageTemplate.objects.get(name="creada_en_manager")
        self.assertEqual(imported.meta_status, "APPROVED")
        self.assertEqual(imported.category, "marketing")
        self.assertEqual(report["created"], 1)

    def test_a_template_only_the_crm_knows_is_left_alone_and_counted(self):
        # A draft not yet submitted to Meta must not be deleted.
        local(name="solo_local")
        report = sync_with([meta_row()])

        self.assertTrue(MessageTemplate.objects.filter(name="solo_local").exists())
        self.assertEqual(report["unmatched"], 1)

    def test_rows_are_matched_on_name_and_language(self):
        spanish = local(language="es", status="pendiente")
        english = local(language="en", name="saludo_inicial", status="pendiente")
        sync_with([meta_row(language="en", status="APPROVED")])

        spanish.refresh_from_db()
        english.refresh_from_db()
        self.assertEqual(english.meta_status, "APPROVED")
        self.assertEqual(spanish.meta_status, "")

    def test_a_row_without_a_name_is_skipped_not_crashed_on(self):
        report = sync_with([{"id": "1", "status": "APPROVED"}, meta_row()])
        self.assertEqual(report["fetched"], 2)
        self.assertEqual(MessageTemplate.objects.count(), 1)

    def test_a_provider_that_cannot_list_templates_says_so(self):
        provider = Mock(spec=[])       # no fetch_templates
        provider.name = "fake"
        with patch.object(services, "get_provider", return_value=provider):
            with self.assertRaises(RuntimeError) as caught:
                services.sync_templates()
        self.assertIn("MESSAGING_PROVIDER=meta", str(caught.exception))


class SendablePicksUpMetasVerdictTests(TestCase):
    """The send dialog must not offer a plantilla WhatsApp would refuse."""

    def test_only_approved_plantillas_are_offered_once_synced(self):
        local(name="aprobada", meta_status="APPROVED", status="aceptada")
        local(name="pausada", meta_status="PAUSED", status="pendiente")
        local(name="deshabilitada", meta_status="DISABLED", status="pendiente")

        names = [t.name for t in services.sendable_templates()]
        self.assertEqual(names, ["aprobada"])

    def test_an_unsynced_plantilla_keeps_the_lenient_mvp_rule(self):
        # No Meta account: a pendiente plantilla is still offered, because
        # this app can create plantillas Meta has never seen.
        local(name="sin_sincronizar", status="pendiente")
        local(name="rechazada_local", status="rechazada")

        names = [t.name for t in services.sendable_templates()]
        self.assertEqual(names, ["sin_sincronizar"])

    def test_the_two_rules_coexist(self):
        local(name="aprobada", meta_status="APPROVED", status="aceptada")
        local(name="pausada", meta_status="PAUSED", status="pendiente")
        local(name="sin_sincronizar", status="pendiente")

        names = sorted(t.name for t in services.sendable_templates())
        self.assertEqual(names, ["aprobada", "sin_sincronizar"])

    def test_the_account_toggle_still_wins(self):
        local(name="apagada", meta_status="APPROVED", status="aceptada", is_active=False)
        self.assertEqual(list(services.sendable_templates()), [])


class FetchTemplatesTests(TestCase):
    def test_it_refuses_to_run_unconfigured(self):
        with self.assertRaises(RuntimeError) as caught:
            MetaProvider().fetch_templates()
        self.assertIn("META_WABA_ID", str(caught.exception))

    def test_the_command_reports_a_failure_instead_of_looking_empty(self):
        with self.assertRaises(CommandError):
            call_command("sync_templates")

    @override_settings(META_ACCESS_TOKEN="tok", META_WABA_ID="123")
    def test_it_follows_metas_paging_to_the_end(self):
        # A WABA holds more templates than one page, and Meta's paging.next
        # is a fully-formed URL carrying its own cursor and query -- sending
        # the original params alongside it would clobber the cursor and loop
        # on page one forever.
        pages = [
            {"data": [meta_row(name="a")],
             "paging": {"next": "https://graph.facebook.com/next-page"}},
            {"data": [meta_row(name="b")], "paging": {}},
        ]
        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append((url, params))
            response = Mock()
            response.json.return_value = pages[len(calls) - 1]
            response.raise_for_status = lambda: None
            return response

        with patch("messaging.providers.meta.requests.get", side_effect=fake_get):
            rows = MetaProvider().fetch_templates()

        self.assertEqual([row["name"] for row in rows], ["a", "b"])
        self.assertTrue(calls[0][0].endswith("/123/message_templates"))
        self.assertIn("fields", calls[0][1])
        self.assertEqual(calls[1][0], "https://graph.facebook.com/next-page")
        self.assertIsNone(calls[1][1])

    @override_settings(META_ACCESS_TOKEN="tok", META_WABA_ID="123")
    def test_an_http_error_is_raised_not_swallowed(self):
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("401 Unauthorized")
        with patch("messaging.providers.meta.requests.get", return_value=response):
            with self.assertRaises(RuntimeError):
                MetaProvider().fetch_templates()
