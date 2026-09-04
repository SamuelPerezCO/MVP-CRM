"""Tests for the Tiempos de Respuesta stat screen: the pairing walk in
core.estadisticas_tiempos, the three filters, and the screen and JSON
endpoint that render it."""

from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core import estadisticas_tiempos as tiempos
from core.estadisticas_volumen import REPORT_TZ
from core.models import Client
from messaging.models import Conversation, Message

TZ = REPORT_TZ


def at(day: date, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    """An aware datetime on `day` at a REPORT_TZ wall clock."""
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=TZ)


class TiemposFormattingTests(TestCase):
    def test_durations_always_spell_all_three_units(self):
        self.assertEqual(tiempos.format_duration(0), "0 hr 0 min 0 s")
        self.assertEqual(tiempos.format_duration(75), "0 hr 1 min 15 s")
        self.assertEqual(tiempos.format_duration(3661), "1 hr 1 min 1 s")

    def test_hours_accumulate_rather_than_rolling_into_days(self):
        self.assertEqual(tiempos.format_duration(27 * 3600 + 62), "27 hr 1 min 2 s")


class TiemposFilterTests(TestCase):
    def test_unknown_platform_means_all(self):
        self.assertEqual(tiempos.parse_platform({"platform": "carrier-pigeon"}), "")
        self.assertEqual(tiempos.parse_platform({}), "")

    def test_known_platform_is_honoured(self):
        self.assertEqual(tiempos.parse_platform({"platform": "whatsapp"}), "whatsapp")

    def test_platform_channels_cover_every_conversation_channel(self):
        covered = {
            channel
            for channels in tiempos.PLATFORM_CHANNELS.values()
            for channel in channels
        }
        self.assertEqual(covered, {key for key, _ in Conversation.CHANNEL_CHOICES})

    def test_agent_parse_falls_back_to_all_on_anything_unusable(self):
        for params in [{}, {"agent": ""}, {"agent": "abc"}, {"agent": "999999"}]:
            with self.subTest(params=params):
                self.assertIsNone(tiempos.parse_agent(params))

    def test_agent_parse_finds_an_active_user(self):
        user = get_user_model().objects.create_user("ana")
        self.assertEqual(tiempos.parse_agent({"agent": str(user.pk)}), user)

    def test_an_inactive_user_is_not_a_filter_option(self):
        user = get_user_model().objects.create_user("expleado", is_active=False)
        self.assertIsNone(tiempos.parse_agent({"agent": str(user.pk)}))


class TiemposReportTests(TestCase):
    """The pairing walk. Uses _build directly so the cache never masks a
    change."""

    def setUp(self):
        cache.clear()
        self.start = date(2026, 8, 1)
        self.end = date(2026, 8, 7)
        self.serial = 0

    def conversation(self, channel="whatsapp") -> Conversation:
        self.serial += 1
        contact = Client.objects.create(
            first_name="Test",
            last_name=str(self.serial),
            phone=f"+57300000{self.serial:04d}",
        )
        return Conversation.objects.create(contact=contact, channel=channel)

    def message(self, conversation, when, direction, sent_by=None):
        self.serial += 1
        return Message.objects.create(
            conversation=conversation,
            direction=direction,
            body="x",
            timestamp=when,
            sent_by=sent_by,
            provider_message_id=f"t-{self.serial}",
        )

    def build(self, agent=None, platform=""):
        return tiempos._build(self.start, self.end, agent, platform)

    def test_empty_period_reports_zeros_without_crashing(self):
        report = self.build()
        tiles = {tile["key"]: tile for tile in report["tiles"]}
        self.assertEqual(tiles["avg"]["value"], "0 hr 0 min 0 s")
        self.assertEqual(tiles["postmia"]["note"], "0 escalaciones en el período")
        self.assertEqual(tiles["measured"]["value"], "0")
        self.assertEqual(report["responses"], 0)
        # One series always: the chart keeps its axis and legend at zero.
        self.assertEqual(len(report["series"]), 1)
        self.assertEqual(report["series"][0]["values"], [0] * len(tiempos.BANDS))

    def test_a_reply_measures_from_the_first_unanswered_inbound(self):
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 10, 4), Message.INBOUND)
        self.message(conversation, at(self.start, 10, 5), Message.OUTBOUND)
        report = self.build()
        # 5 minutes from the run's first message, not 1 from its last.
        self.assertEqual(
            {t["key"]: t["value"] for t in report["tiles"]}["avg"], "0 hr 5 min 0 s"
        )
        self.assertEqual(report["responses"], 1)

    def test_only_the_first_outbound_after_a_run_is_a_response(self):
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10), Message.INBOUND)
        self.message(conversation, at(self.start, 11), Message.OUTBOUND)
        self.message(conversation, at(self.start, 12), Message.OUTBOUND)
        self.assertEqual(self.build()["responses"], 1)

    def test_conversations_do_not_bleed_into_each_other(self):
        # An inbound in one chat then an outbound in another pairs nothing.
        self.message(self.conversation(), at(self.start, 10), Message.INBOUND)
        self.message(self.conversation(), at(self.start, 11), Message.OUTBOUND)
        self.assertEqual(self.build()["responses"], 0)

    def test_measured_counts_conversations_not_responses(self):
        conversation = self.conversation()
        for hour in (9, 11, 13):
            self.message(conversation, at(self.start, hour), Message.INBOUND)
            self.message(conversation, at(self.start, hour, 30), Message.OUTBOUND)
        report = self.build()
        self.assertEqual(report["responses"], 3)
        self.assertEqual(report["measured"], 1)

    def test_escalation_is_a_human_reply_right_after_an_automated_one(self):
        user = get_user_model().objects.create_user("ana")
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 10, 1), Message.OUTBOUND)  # bot
        self.message(
            conversation, at(self.start, 10, 4), Message.OUTBOUND, sent_by=user
        )
        tiles = {t["key"]: t for t in self.build()["tiles"]}
        self.assertEqual(tiles["postmia"]["value"], "0 hr 3 min 0 s")
        self.assertEqual(tiles["postmia"]["note"], "1 escalación en el período")

    def test_a_customer_message_in_between_breaks_the_escalation(self):
        user = get_user_model().objects.create_user("ana")
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 10, 1), Message.OUTBOUND)  # bot
        self.message(conversation, at(self.start, 10, 2), Message.INBOUND)
        self.message(
            conversation, at(self.start, 10, 4), Message.OUTBOUND, sent_by=user
        )
        tiles = {t["key"]: t for t in self.build()["tiles"]}
        self.assertEqual(tiles["postmia"]["note"], "0 escalaciones en el período")

    def test_two_human_replies_in_a_row_are_not_an_escalation(self):
        user = get_user_model().objects.create_user("ana")
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(
            conversation, at(self.start, 10, 1), Message.OUTBOUND, sent_by=user
        )
        self.message(
            conversation, at(self.start, 10, 4), Message.OUTBOUND, sent_by=user
        )
        tiles = {t["key"]: t for t in self.build()["tiles"]}
        self.assertEqual(tiles["postmia"]["note"], "0 escalaciones en el período")

    def test_agent_filter_keeps_only_that_agents_responses(self):
        ana = get_user_model().objects.create_user("ana")
        leo = get_user_model().objects.create_user("leo")
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(
            conversation, at(self.start, 10, 5), Message.OUTBOUND, sent_by=ana
        )
        other = self.conversation()
        self.message(other, at(self.start, 11, 0), Message.INBOUND)
        self.message(other, at(self.start, 11, 30), Message.OUTBOUND, sent_by=leo)

        report = self.build(agent=ana)
        self.assertEqual(report["responses"], 1)
        self.assertEqual(report["measured"], 1)
        tiles = {t["key"]: t for t in report["tiles"]}
        self.assertEqual(tiles["avg"]["value"], "0 hr 5 min 0 s")
        self.assertEqual(tiles["avg"]["note"], "Respuestas de ana")

    def test_platform_filter_folds_channels_like_the_volumen_chart(self):
        # Facebook counts as Messenger, so a "messenger" filter keeps it.
        conversation = self.conversation(channel="facebook")
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 10, 5), Message.OUTBOUND)
        other = self.conversation(channel="whatsapp")
        self.message(other, at(self.start, 11, 0), Message.INBOUND)
        self.message(other, at(self.start, 11, 30), Message.OUTBOUND)

        self.assertEqual(self.build(platform="messenger")["responses"], 1)
        self.assertEqual(self.build(platform="whatsapp")["responses"], 1)
        self.assertEqual(self.build()["responses"], 2)

    def test_distribution_lands_each_gap_in_its_band(self):
        conversation = self.conversation()
        # 2 minutes -> "< 5 min"; ~2 hours -> "1 - 4 h".
        self.message(conversation, at(self.start, 9, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 9, 2), Message.OUTBOUND)
        self.message(conversation, at(self.start, 12, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 14, 0), Message.OUTBOUND)
        values = dict(zip(self.build()["bands"], self.build()["series"][0]["values"]))
        self.assertEqual(values["< 5 min"], 50)
        self.assertEqual(values["1 - 4 h"], 50)
        self.assertEqual(values["> 24 h"], 0)

    def test_postmia_series_only_appears_when_there_are_escalations(self):
        conversation = self.conversation()
        self.message(conversation, at(self.start, 10, 0), Message.INBOUND)
        self.message(conversation, at(self.start, 10, 5), Message.OUTBOUND)
        report = self.build()
        self.assertEqual([s["key"] for s in report["series"]], ["promedio"])
        # And the table has one value column per drawn series.
        self.assertEqual(len(report["table"][0]["values"]), 1)

    def test_series_carry_their_own_dark_steps(self):
        for entry in tiempos.SERIES:
            with self.subTest(entry["key"]):
                self.assertNotEqual(entry["light"], entry["dark"])

    def test_messages_outside_the_period_are_excluded(self):
        conversation = self.conversation()
        self.message(
            conversation, at(self.start - timedelta(days=1), 10), Message.INBOUND
        )
        self.message(
            conversation, at(self.start - timedelta(days=1), 11), Message.OUTBOUND
        )
        self.assertEqual(self.build()["responses"], 0)


class TiemposCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_each_filter_combination_is_its_own_entry(self):
        start, end = date(2026, 8, 1), date(2026, 8, 7)
        all_platforms = tiempos.report(start, end, None, "")
        whatsapp = tiempos.report(start, end, None, "whatsapp")
        self.assertEqual(all_platforms["platform"], "")
        self.assertEqual(whatsapp["platform"], "whatsapp")

    def test_a_combination_is_only_computed_once(self):
        start, end = date(2026, 8, 1), date(2026, 8, 7)
        first = tiempos.report(start, end, None, "")
        contact = Client.objects.create(
            first_name="Cache", last_name="Test", phone="+573000000998"
        )
        conversation = Conversation.objects.create(contact=contact, channel="whatsapp")
        Message.objects.create(
            conversation=conversation, direction=Message.INBOUND,
            body="x", timestamp=at(start), provider_message_id="cache-t1",
        )
        Message.objects.create(
            conversation=conversation, direction=Message.OUTBOUND,
            body="x", timestamp=at(start, 13), provider_message_id="cache-t2",
        )
        self.assertEqual(tiempos.report(start, end, None, ""), first)


class TiemposScreenTests(TestCase):
    def setUp(self):
        cache.clear()

    def get(self, **params):
        return self.client.get(
            reverse("estadisticas_card", args=["tiempos-respuesta"]), params
        )

    def test_the_card_route_now_renders_the_real_screen(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-tiempos-root")
        self.assertNotContains(response, "próximamente")

    def test_header_title_subtitle_and_how_it_works(self):
        response = self.get()
        self.assertContains(response, "Tiempos de Respuesta")
        self.assertContains(
            response,
            "Métricas de velocidad de respuesta de tu equipo, incluyendo "
            "tiempos post-MIA",
        )
        self.assertContains(response, "¿Cómo funciona?")
        self.assertContains(response, 'data-dialog-open="tiempos-howto-dialog"')
        self.assertContains(response, 'id="tiempos-howto-dialog"')

    def test_no_template_syntax_leaks_into_the_output(self):
        html = self.get().content.decode()
        self.assertNotIn("{#", html)
        self.assertNotIn("{%", html)

    def test_all_three_filters_are_present(self):
        html = self.get().content.decode()
        self.assertIn("data-filter-agent", html)
        self.assertIn("data-filter-platform", html)
        self.assertEqual(html.count("data-daterange"), 1)
        self.assertIn("Todos los agentes", html)
        self.assertIn("Todas las plataformas", html)

    def test_the_three_tiles_render_with_label_value_and_note(self):
        html = self.get().content.decode()
        self.assertEqual(html.count('class="kpi kpi--'), 3)
        for key in ["avg", "postmia", "measured"]:
            with self.subTest(key):
                self.assertIn(f'data-kpi-value="{key}"', html)
                self.assertIn(f'data-kpi-note="{key}"', html)
        for label in ["Tiempo promedio", "Post-MIA promedio",
                      "Conversaciones medidas"]:
            self.assertIn(label, html)

    def test_tiles_are_split_into_two_labelled_groups(self):
        html = self.get().content.decode()
        self.assertIn("Tiempos de respuesta", html)
        self.assertIn("Resumen", html)
        self.assertIn("kpi-group--total", html)

    def test_chart_payload_and_accessible_table_are_both_present(self):
        html = self.get().content.decode()
        self.assertIn('id="tiempos-report"', html)
        self.assertIn("data-chart", html)
        self.assertIn("data-table-toggle", html)
        self.assertIn("Ver tabla", html)
        self.assertIn('class="data-table"', html)

    def test_the_filters_query_is_honoured(self):
        user = get_user_model().objects.create_user("ana")
        response = self.get(
            start="2026-08-01", end="2026-08-07",
            agent=str(user.pk), platform="whatsapp",
        )
        self.assertContains(response, "01/08/26 - 07/08/26")
        html = response.content.decode()
        self.assertIn(f'value="{user.pk}"\n                  selected', html)
        self.assertIn('value="whatsapp"\n                  selected', html)

    def test_it_loads_its_own_bundle(self):
        html = self.get().content.decode()
        for src in ["vendor/echarts.min.js", "stats_chart.js", "stats_tiempos.js"]:
            self.assertIn(src, html)


class TiemposDataEndpointTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_returns_the_report_as_json(self):
        response = self.client.get(
            reverse("estadisticas_tiempos_data"),
            {"start": "2026-08-01", "end": "2026-08-07"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["start"], "2026-08-01")
        self.assertEqual(data["range_label"], "01/08/26 - 07/08/26")
        self.assertEqual(len(data["tiles"]), 3)
        self.assertEqual(len(data["bands"]), len(tiempos.BANDS))
        self.assertIn("table", data)

    def test_unusable_filters_fall_back_and_say_what_they_used(self):
        response = self.client.get(
            reverse("estadisticas_tiempos_data"),
            {"start": "zzz", "end": "zzz", "agent": "zzz", "platform": "fax"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        start, end = tiempos.default_range()
        self.assertEqual(data["start"], start.isoformat())
        self.assertEqual(data["end"], end.isoformat())
        self.assertEqual(data["agent"], "")
        self.assertEqual(data["platform"], "")

    def test_the_endpoint_is_not_swallowed_by_the_card_route(self):
        self.assertEqual(
            reverse("estadisticas_tiempos_data"),
            "/estadisticas/mensajeria/tiempos/datos/",
        )
