"""Tests for the Volumen de Mensajes stat screen: the ORM aggregation in
core.estadisticas_volumen, the period filter, and the screen and JSON
endpoint that render it."""

from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core import estadisticas_volumen as volumen
from core.models import Client
from messaging.models import Conversation, Message

TZ = volumen.REPORT_TZ


def at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    """An aware datetime on `day` at a REPORT_TZ wall clock."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


class VolumenFormattingTests(TestCase):
    def test_thousands_use_spanish_separators(self):
        self.assertEqual(volumen.format_number(15268), "15.268")
        self.assertEqual(volumen.format_number(41355), "41.355")
        self.assertEqual(volumen.format_number(0), "0")

    def test_hour_band_collapses_a_shared_suffix(self):
        self.assertEqual(volumen.format_hour_band(10), "10 - 11 a.m.")
        self.assertEqual(volumen.format_hour_band(15), "3 - 4 p.m.")

    def test_hour_band_spells_out_both_when_they_differ(self):
        self.assertEqual(volumen.format_hour_band(11), "11 a.m. - 12 p.m.")
        self.assertEqual(volumen.format_hour_band(23), "11 p.m. - 12 a.m.")

    def test_midnight_is_twelve_not_zero(self):
        self.assertEqual(volumen.format_hour_band(0), "12 - 1 a.m.")

    def test_range_label_is_day_month_two_digit_year(self):
        self.assertEqual(
            volumen.format_range(date(2026, 7, 28), date(2026, 8, 28)),
            "28/07/26 - 28/08/26",
        )


class VolumenRangeTests(TestCase):
    def test_default_is_thirty_days_inclusive(self):
        start, end = volumen.default_range()
        self.assertEqual((end - start).days + 1, volumen.DEFAULT_RANGE_DAYS)
        self.assertEqual(end, volumen.today())

    def test_explicit_range_is_honoured(self):
        self.assertEqual(
            volumen.parse_range({"start": "2026-08-01", "end": "2026-08-07"}),
            (date(2026, 8, 1), date(2026, 8, 7)),
        )

    def test_missing_or_malformed_falls_back_rather_than_erroring(self):
        default = volumen.default_range()
        for params in [
            {},
            {"start": "2026-08-01"},
            {"start": "nonsense", "end": "2026-08-07"},
            {"start": "2026-13-45", "end": "2026-08-07"},
        ]:
            with self.subTest(params=params):
                self.assertEqual(volumen.parse_range(params), default)

    def test_inverted_range_falls_back(self):
        self.assertEqual(
            volumen.parse_range({"start": "2026-08-07", "end": "2026-08-01"}),
            volumen.default_range(),
        )

    def test_absurdly_wide_range_is_capped_back_to_the_default(self):
        self.assertEqual(
            volumen.parse_range({"start": "2000-01-01", "end": "2026-08-01"}),
            volumen.default_range(),
        )


class VolumenChannelMapTests(TestCase):
    def test_every_conversation_channel_has_a_home(self):
        # The module asserts this at import; pin it as a test too, so adding
        # a channel to the model fails here with a readable message rather
        # than silently dropping its messages out of the totals.
        self.assertEqual(
            set(volumen.SOURCE_CHANNEL),
            {key for key, _ in Conversation.CHANNEL_CHOICES},
        )

    def test_every_mapped_channel_has_a_palette_entry(self):
        for target in set(volumen.SOURCE_CHANNEL.values()):
            self.assertIn(target, volumen.CHANNEL_BY_KEY)

    def test_palette_uses_the_validated_steps(self):
        expected_light = {
            "whatsapp": "#1DA851",
            "messenger": "#0084FF",
            "instagram": "#E4405F",
        }
        expected_dark = {
            "whatsapp": "#16A34A",
            "messenger": "#3B82F6",
            "instagram": "#EA580C",
        }
        for key, light in expected_light.items():
            with self.subTest(key):
                self.assertEqual(volumen.CHANNEL_BY_KEY[key].light, light)
                self.assertEqual(volumen.CHANNEL_BY_KEY[key].dark, expected_dark[key])

    def test_dark_steps_are_their_own_values_not_the_light_ones(self):
        for channel in volumen.CHANNELS:
            with self.subTest(channel.key):
                self.assertNotEqual(channel.light, channel.dark)


class VolumenReportTests(TestCase):
    """Aggregation. Uses _build directly so the cache never masks a change."""

    def setUp(self):
        cache.clear()
        self.start = date(2026, 8, 1)
        self.end = date(2026, 8, 7)
        self.conversations = {}

    def conversation(self, channel: str) -> Conversation:
        if channel not in self.conversations:
            contact = Client.objects.create(
                first_name="Test", last_name=channel, phone=f"+5730000009{len(self.conversations)}"
            )
            self.conversations[channel] = Conversation.objects.create(
                contact=contact, channel=channel
            )
        return self.conversations[channel]

    def add(self, channel, when, direction=Message.INBOUND, count=1):
        for index in range(count):
            Message.objects.create(
                conversation=self.conversation(channel),
                direction=direction,
                body="x",
                timestamp=when,
                provider_message_id=f"t-{channel}-{when.isoformat()}-{direction}-{index}",
            )

    def report(self):
        return volumen._build(self.start, self.end)

    def test_empty_period_reports_zeros_without_crashing(self):
        report = self.report()
        self.assertEqual(report["totals"]["total"], 0)
        self.assertEqual(report["totals"]["received_share"], 0)
        self.assertIsNone(report["peak_hour"])
        self.assertEqual(report["series"], [])
        # The day axis is still the full period, so the chart has an x-axis.
        self.assertEqual(len(report["days"]), 7)

    def test_empty_period_tiles_read_zero_and_a_dash(self):
        tiles = {tile["key"]: tile for tile in self.report()["tiles"]}
        self.assertEqual(tiles["received"]["value"], "0")
        self.assertEqual(tiles["total"]["value"], "0")
        self.assertEqual(tiles["peak"]["value"], "—")
        self.assertEqual(tiles["peak"]["note"], "Sin actividad")

    def test_direction_split_and_shares(self):
        self.add("whatsapp", at(self.start), Message.INBOUND, count=37)
        self.add("whatsapp", at(self.start), Message.OUTBOUND, count=63)
        totals = self.report()["totals"]
        self.assertEqual(totals["received"], 37)
        self.assertEqual(totals["sent"], 63)
        self.assertEqual(totals["total"], 100)
        self.assertEqual(totals["received_share"], 37)
        self.assertEqual(totals["sent_share"], 63)

    def test_one_point_per_day_with_gaps_as_zero(self):
        self.add("whatsapp", at(self.start), count=3)
        self.add("whatsapp", at(self.end), count=5)
        series = self.report()["series"]
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["values"], [3, 0, 0, 0, 0, 0, 5])

    def test_channels_fold_onto_their_chart_series(self):
        # Facebook joins Messenger; Instagram DMs join Instagram.
        self.add("messenger", at(self.start), count=2)
        self.add("facebook", at(self.start), count=3)
        self.add("instagram-dm", at(self.start), count=4)
        self.add("instagram", at(self.start), count=1)
        series = {entry["key"]: entry for entry in self.report()["series"]}
        self.assertEqual(series["messenger"]["values"][0], 5)
        self.assertEqual(series["instagram"]["values"][0], 5)

    def test_a_channel_with_no_data_drops_out_and_survivors_keep_their_color(self):
        self.add("whatsapp", at(self.start))
        self.add("instagram-dm", at(self.start))
        series = self.report()["series"]
        # Messenger has nothing, so it is not drawn...
        self.assertEqual([entry["key"] for entry in series], ["whatsapp", "instagram"])
        # ...and Instagram still carries Instagram's color, not Messenger's.
        by_key = {entry["key"]: entry for entry in series}
        self.assertEqual(by_key["instagram"]["light"], "#E4405F")
        self.assertEqual(by_key["whatsapp"]["light"], "#1DA851")

    def test_peak_hour_sums_the_same_hour_across_days(self):
        # 2 messages at 10am on each of three days beats 5 at 3pm on one day.
        for offset in range(3):
            self.add("whatsapp", at(self.start + timedelta(days=offset), 10), count=2)
        self.add("whatsapp", at(self.start, 15), count=5)
        peak = self.report()["peak_hour"]
        self.assertEqual(peak["hour"], 10)
        self.assertEqual(peak["total"], 6)
        self.assertEqual(peak["label"], "10 - 11 a.m.")

    def test_days_are_bucketed_in_report_tz_not_utc(self):
        # 22:00 Bogotá on the 1st is 03:00 UTC on the 2nd. It belongs to the
        # 1st's point.
        self.add("whatsapp", at(self.start, 22))
        self.assertEqual(self.report()["series"][0]["values"][0], 1)

    def test_messages_outside_the_period_are_excluded_at_both_ends(self):
        self.add("whatsapp", at(self.start - timedelta(days=1), 23, 59))
        self.add("whatsapp", at(self.end + timedelta(days=1), 0, 1))
        self.assertEqual(self.report()["totals"]["total"], 0)

    def test_the_last_day_is_included_to_its_final_minute(self):
        self.add("whatsapp", at(self.end, 23, 59))
        self.assertEqual(self.report()["totals"]["total"], 1)

    def test_table_matches_the_chart_series(self):
        self.add("whatsapp", at(self.start), count=2)
        self.add("messenger", at(self.start), count=3)
        report = self.report()
        first = report["table"][0]
        self.assertEqual(first["values"], ["2", "3"])
        self.assertEqual(first["total"], "5")
        self.assertEqual(len(report["table"]), len(report["days"]))
        # One column per drawn series, in the same order.
        self.assertEqual(
            len(first["values"]), len(report["series"])
        )

    def test_tile_numbers_are_formatted_spanish(self):
        self.add("whatsapp", at(self.start), Message.INBOUND, count=1500)
        tiles = {tile["key"]: tile for tile in self.report()["tiles"]}
        self.assertEqual(tiles["received"]["value"], "1.500")
        self.assertEqual(tiles["total"]["value"], "1.500")


class VolumenCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_range_is_only_aggregated_once(self):
        start, end = date(2026, 8, 1), date(2026, 8, 7)
        first = volumen.report(start, end)
        # A message added after the first call must not show up until the
        # entry expires -- that is what "cached" means here.
        contact = Client.objects.create(
            first_name="Cache", last_name="Test", phone="+573000000999"
        )
        conversation = Conversation.objects.create(contact=contact, channel="whatsapp")
        Message.objects.create(
            conversation=conversation, direction=Message.INBOUND,
            body="x", timestamp=at(start), provider_message_id="cache-1",
        )
        self.assertEqual(volumen.report(start, end), first)

    def test_a_different_range_is_its_own_entry(self):
        volumen.report(date(2026, 8, 1), date(2026, 8, 7))
        other = volumen.report(date(2026, 9, 1), date(2026, 9, 7))
        self.assertEqual(other["start"], "2026-09-01")


class VolumenScreenTests(TestCase):
    def setUp(self):
        cache.clear()

    def get(self, **params):
        return self.client.get(
            reverse("estadisticas_card", args=["volumen-mensajes"]), params
        )

    def test_the_card_route_now_renders_the_real_screen(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-volumen-root")
        self.assertNotContains(response, "próximamente")

    def test_header_title_subtitle_and_how_it_works(self):
        response = self.get()
        self.assertContains(response, "Volumen de Mensajes")
        self.assertContains(
            response,
            "Cantidad de mensajes agregados por plataforma y horarios de mayor actividad",
        )
        self.assertContains(response, "¿Cómo funciona?")
        self.assertContains(response, 'data-dialog-open="volumen-howto-dialog"')
        self.assertContains(response, 'id="volumen-howto-dialog"')

    def test_back_link_returns_to_the_card_grid(self):
        url = reverse("estadisticas_panel", args=["mensajeria"])
        html = self.get().content.decode()
        self.assertIn(f'hx-get="{url}"', html)
        self.assertIn("Estadísticas de mensajería", html)

    def test_no_template_syntax_leaks_into_the_output(self):
        # Django's {# #} comment is single-line only: a multi-line one is not
        # a comment and renders as visible text on the page.
        html = self.get().content.decode()
        self.assertNotIn("{#", html)
        self.assertNotIn("{%", html)

    def test_exactly_one_period_filter_governs_the_page(self):
        html = self.get().content.decode()
        self.assertEqual(html.count("data-daterange"), 1)
        self.assertIn("Periodo", html)

    def test_all_four_tiles_render_with_label_value_and_note(self):
        html = self.get().content.decode()
        self.assertEqual(html.count('class="kpi kpi--'), 4)
        for key in ["received", "sent", "peak", "total"]:
            with self.subTest(key):
                self.assertIn(f'data-kpi-value="{key}"', html)
                self.assertIn(f'data-kpi-note="{key}"', html)
        for label in ["Mensajes recibidos", "Mensajes enviados",
                      "Hora pico", "Mensajes totales"]:
            self.assertIn(label, html)

    def test_tiles_are_split_into_two_labelled_groups(self):
        html = self.get().content.decode()
        self.assertIn("Volumen del período", html)
        self.assertIn(">Total<", html)
        self.assertIn("kpi-group--total", html)

    def test_every_tile_carries_an_info_tooltip(self):
        html = self.get().content.decode()
        # Four tiles plus the chart's own dot.
        self.assertEqual(html.count("data-tip="), 5)

    def test_chart_payload_and_accessible_table_are_both_present(self):
        html = self.get().content.decode()
        self.assertIn('id="volumen-report"', html)
        self.assertIn("data-chart", html)
        self.assertIn("data-table-toggle", html)
        self.assertIn("Ver tabla", html)
        self.assertIn('class="data-table"', html)

    def test_chart_is_labelled_and_points_at_its_table(self):
        # The canvas is unreadable to assistive tech, so it is labelled as an
        # image and its label names the table that carries the same data.
        html = self.get().content.decode()
        chart_tag = html.split("data-chart ", 1)[1].split(">", 1)[0]
        self.assertIn('role="img"', chart_tag)
        self.assertIn("Ver tabla", chart_tag)

    def test_the_period_query_is_honoured(self):
        response = self.get(start="2026-08-01", end="2026-08-07")
        self.assertContains(response, "01/08/26 - 07/08/26")
        self.assertContains(response, 'value="2026-08-01"')
        self.assertContains(response, 'value="2026-08-07"')

    def test_the_screen_is_a_bare_fragment(self):
        html = self.get().content.decode()
        self.assertNotIn("<html", html)
        self.assertNotIn("side-nav", html)

    def test_it_loads_its_own_bundle_rather_than_base_html(self):
        html = self.get().content.decode()
        for src in ["vendor/echarts.min.js", "stats_chart.js", "stats_volumen.js"]:
            self.assertIn(src, html)


class VolumenDataEndpointTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_returns_the_report_as_json(self):
        response = self.client.get(
            reverse("estadisticas_volumen_data"),
            {"start": "2026-08-01", "end": "2026-08-07"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["start"], "2026-08-01")
        self.assertEqual(data["end"], "2026-08-07")
        self.assertEqual(data["range_label"], "01/08/26 - 07/08/26")
        self.assertEqual(len(data["days"]), 7)
        self.assertEqual(len(data["tiles"]), 4)
        self.assertIn("table", data)

    def test_an_unusable_range_falls_back_and_says_which_it_used(self):
        response = self.client.get(
            reverse("estadisticas_volumen_data"), {"start": "zzz", "end": "zzz"}
        )
        self.assertEqual(response.status_code, 200)
        start, end = volumen.default_range()
        self.assertEqual(response.json()["start"], start.isoformat())
        self.assertEqual(response.json()["end"], end.isoformat())

    def test_no_params_is_the_default_window(self):
        data = self.client.get(reverse("estadisticas_volumen_data")).json()
        self.assertEqual(len(data["days"]), volumen.DEFAULT_RANGE_DAYS)

    def test_the_endpoint_is_not_swallowed_by_the_card_route(self):
        # "estadisticas/mensajeria/volumen/datos/" must not resolve as a card.
        self.assertEqual(
            reverse("estadisticas_volumen_data"),
            "/estadisticas/mensajeria/volumen/datos/",
        )
