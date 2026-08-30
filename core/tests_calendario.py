"""Tests for the Mi calendario screen: the panel with its sidebar, the JSON
events feed and its timezone contract (store UTC, render Bogotá), event
mutations from the modal and the grid, and the session preferences."""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import TestCase
from django.urls import reverse

from core.calendario import CALENDAR_TZ
from core.models import CalendarEvent, Client


def make_event(title="Reunión demo", start=None, minutes=60, **kwargs):
    """One event; tests create their own data, there are no seeds."""
    start = start or datetime(2026, 8, 28, 9, 0, tzinfo=CALENDAR_TZ)
    return CalendarEvent.objects.create(
        title=title, start=start, end=start + timedelta(minutes=minutes), **kwargs
    )


class CalendarPanelTests(TestCase):
    def test_panel_renders_sidebar_grid_and_modal(self):
        html = self.client.get(
            reverse("crm_panel", args=["mi-calendario"])
        ).content.decode()
        for fragment in (
            "Crear +", "Mostrar fines de semana", "Duración del slot",
            "data-calendar-grid", "data-mini-cal", 'id="event-dialog"',
            "vendor/fullcalendar.min.js", "js/calendario.js",
        ):
            with self.subTest(fragment):
                self.assertIn(fragment, html)

    def test_section_route_reaches_the_panel(self):
        response = self.client.get(
            reverse("section", args=["crm"]), {"view": "mi-calendario"}
        )
        self.assertContains(response, "data-calendar-root")

    def test_prefs_render_from_the_session(self):
        session = self.client.session
        session["calendar_weekends"] = False
        session["calendar_slot"] = "01:00:00"
        session.save()
        html = self.client.get(
            reverse("crm_panel", args=["mi-calendario"])
        ).content.decode()
        self.assertIn('data-weekends="0"', html)
        self.assertIn('data-slot="01:00:00"', html)

    def test_contacts_populate_the_picker(self):
        Client.objects.create(first_name="Camila", last_name="Pruebas", phone="+571")
        html = self.client.get(
            reverse("crm_panel", args=["mi-calendario"])
        ).content.decode()
        self.assertIn("Camila Pruebas", html)


class CalendarEventsFeedTests(TestCase):
    def feed(self, start="2026-08-24T00:00:00", end="2026-08-31T00:00:00"):
        return self.client.get(reverse("calendar_events"), {"start": start, "end": end})

    def test_nine_bogota_comes_back_as_nine_not_fourteen(self):
        # The classic timezone bug: stored as 14:00 UTC, rendered as the
        # 09:00 Bogotá wall clock it was entered as.
        make_event(start=datetime(2026, 8, 28, 9, 0, tzinfo=CALENDAR_TZ))
        stored = CalendarEvent.objects.get()
        self.assertEqual(stored.start.astimezone(dt_timezone.utc).hour, 14)

        data = self.feed().json()
        self.assertEqual(data[0]["start"], "2026-08-28T09:00:00-05:00")
        self.assertEqual(data[0]["end"], "2026-08-28T10:00:00-05:00")

    def test_only_overlapping_events_return(self):
        make_event(title="Dentro")
        make_event(
            title="Fuera", start=datetime(2026, 9, 10, 9, 0, tzinfo=CALENDAR_TZ)
        )
        titles = [event["title"] for event in self.feed().json()]
        self.assertEqual(titles, ["Dentro"])

    def test_offset_range_params_are_honored(self):
        # FullCalendar may send the range with an explicit offset.
        make_event()
        data = self.feed(
            "2026-08-24T00:00:00-05:00", "2026-08-31T00:00:00-05:00"
        ).json()
        self.assertEqual(len(data), 1)

    def test_bad_range_is_a_400(self):
        self.assertEqual(self.feed(start="garbage").status_code, 400)
        response = self.client.get(reverse("calendar_events"))
        self.assertEqual(response.status_code, 400)

    def test_all_day_events_serialize_as_dates(self):
        start = datetime(2026, 8, 28, 0, 0, tzinfo=CALENDAR_TZ)
        make_event(title="Feria", start=start, minutes=24 * 60, all_day=True)
        data = self.feed().json()
        self.assertEqual(data[0]["start"], "2026-08-28")
        self.assertEqual(data[0]["end"], "2026-08-29")
        self.assertTrue(data[0]["allDay"])

    def test_event_type_and_contact_travel(self):
        contact = Client.objects.create(first_name="Camila", phone="+571")
        make_event(event_type="llamada", contact=contact)
        data = self.feed().json()
        # Class names are keyed by PALETTE COLOR (llamada -> green).
        self.assertEqual(data[0]["classNames"], ["cal-event--green"])
        self.assertEqual(data[0]["extendedProps"]["contactName"], "Camila")
        self.assertEqual(data[0]["extendedProps"]["contactId"], contact.pk)

    def test_oversized_or_inverted_ranges_are_rejected(self):
        make_event()
        for start, end in (
            ("1900-01-01T00:00:00", "2100-01-01T00:00:00"),  # table dump
            ("2026-08-31T00:00:00", "2026-08-24T00:00:00"),  # inverted
            ("9999-12-31T23:00:00", "9999-12-31T23:30:00"),  # overflow
        ):
            with self.subTest(start=start):
                self.assertEqual(self.feed(start, end).status_code, 400)


class CalendarEventMutationTests(TestCase):
    def payload(self, **overrides):
        payload = {
            "title": "Llamada con Camila",
            "date": "2026-08-28",
            "start_time": "09:00",
            "end_time": "09:30",
            "all_day": "0",
            "event_type": "llamada",
            "contact": "",
            "assigned_to": "",
            "description": "",
            "reminder": "15",
        }
        payload.update(overrides)
        return payload

    def test_create_stores_utc_from_bogota_wall_clock(self):
        response = self.client.post(
            reverse("calendar_event_create"), self.payload()
        )
        self.assertEqual(response.status_code, 200)
        event = CalendarEvent.objects.get()
        self.assertEqual(event.start.astimezone(dt_timezone.utc).hour, 14)
        self.assertEqual(event.reminder_minutes_before, 15)
        # The echo renders back in Bogotá for the optimistic UI.
        self.assertEqual(
            response.json()["event"]["start"], "2026-08-28T09:00:00-05:00"
        )

    def test_title_is_required(self):
        response = self.client.post(
            reverse("calendar_event_create"), self.payload(title="  ")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json()["errors"])
        self.assertEqual(CalendarEvent.objects.count(), 0)

    def test_end_must_follow_start(self):
        response = self.client.post(
            reverse("calendar_event_create"), self.payload(end_time="08:00")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("when", response.json()["errors"])

    def test_unknown_contact_is_rejected(self):
        response = self.client.post(
            reverse("calendar_event_create"), self.payload(contact="9999")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("contact", response.json()["errors"])

    def test_all_day_spans_the_whole_bogota_day(self):
        self.client.post(
            reverse("calendar_event_create"),
            self.payload(all_day="1", start_time="", end_time=""),
        )
        event = CalendarEvent.objects.get()
        self.assertTrue(event.all_day)
        self.assertEqual(
            event.start, datetime(2026, 8, 28, 0, 0, tzinfo=CALENDAR_TZ)
        )
        self.assertEqual(event.end - event.start, timedelta(days=1))

    def test_multi_day_all_day_uses_the_inclusive_end_date(self):
        self.client.post(
            reverse("calendar_event_create"),
            self.payload(all_day="1", start_time="", end_time="", end_date="2026-08-30"),
        )
        event = CalendarEvent.objects.get()
        self.assertEqual(event.end - event.start, timedelta(days=3))  # exclusive

    def test_event_ending_at_midnight_rolls_to_the_next_day(self):
        self.client.post(
            reverse("calendar_event_create"),
            self.payload(start_time="22:00", end_time="00:00"),
        )
        event = CalendarEvent.objects.get()
        self.assertEqual(event.end - event.start, timedelta(hours=2))

    def test_off_menu_reminder_is_rejected(self):
        response = self.client.post(
            reverse("calendar_event_create"), self.payload(reminder="7")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("reminder", response.json()["errors"])
        # Astronomically large values must not 500 either.
        response = self.client.post(
            reverse("calendar_event_create"), self.payload(reminder=str(2**64))
        )
        self.assertEqual(response.status_code, 400)

    def test_year_9999_wall_clock_is_an_error_not_a_500(self):
        response = self.client.post(
            reverse("calendar_event_create"),
            self.payload(date="9999-12-31", start_time="20:00", end_time="21:00"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("when", response.json()["errors"])

    def test_update_edits_in_place(self):
        event = make_event()
        self.client.post(
            reverse("calendar_event_update", args=[event.pk]),
            self.payload(title="Cambiada", start_time="11:00", end_time="12:00"),
        )
        event.refresh_from_db()
        self.assertEqual(event.title, "Cambiada")
        self.assertEqual(event.start.astimezone(CALENDAR_TZ).hour, 11)

    def test_move_persists_drag_and_resize(self):
        event = make_event()
        response = self.client.post(
            reverse("calendar_event_move", args=[event.pk]),
            # The client sends coerced wall clocks (naive strings).
            {"start": "2026-08-29T10:00:00", "end": "2026-08-29T11:30:00", "all_day": "0"},
        )
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(
            event.start, datetime(2026, 8, 29, 10, 0, tzinfo=CALENDAR_TZ)
        )

    def test_move_rejects_inverted_ranges(self):
        event = make_event()
        response = self.client.post(
            reverse("calendar_event_move", args=[event.pk]),
            {"start": "2026-08-29T10:00:00", "end": "2026-08-29T09:00:00"},
        )
        self.assertEqual(response.status_code, 400)

    def test_move_into_the_all_day_lane_snaps_to_midnights(self):
        # FullCalendar drops the end on a lane-crossing drag; the client
        # synthesizes end == start + something, but even a degenerate
        # payload must normalize instead of failing.
        event = make_event()
        response = self.client.post(
            reverse("calendar_event_move", args=[event.pk]),
            {"start": "2026-08-29T00:00:00", "end": "2026-08-29T00:00:00", "all_day": "1"},
        )
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertTrue(event.all_day)
        self.assertEqual(
            event.start, datetime(2026, 8, 29, 0, 0, tzinfo=CALENDAR_TZ)
        )
        self.assertEqual(event.end - event.start, timedelta(days=1))

    def test_move_out_of_the_all_day_lane_gets_a_default_hour(self):
        event = make_event(all_day=True)
        response = self.client.post(
            reverse("calendar_event_move", args=[event.pk]),
            {"start": "2026-08-29T10:00:00", "end": "2026-08-29T10:00:00", "all_day": "0"},
        )
        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertFalse(event.all_day)
        self.assertEqual(event.end - event.start, timedelta(hours=1))

    def test_delete_removes_the_event(self):
        event = make_event()
        self.client.post(reverse("calendar_event_delete", args=[event.pk]))
        self.assertEqual(CalendarEvent.objects.count(), 0)

    def test_mutations_reject_get(self):
        event = make_event()
        for name, args in (
            ("calendar_event_create", []),
            ("calendar_event_update", [event.pk]),
            ("calendar_event_move", [event.pk]),
            ("calendar_event_delete", [event.pk]),
            ("calendar_prefs", []),
        ):
            with self.subTest(name):
                self.assertEqual(
                    self.client.get(reverse(name, args=args)).status_code, 405
                )


class CalendarPrefsTests(TestCase):
    def test_prefs_persist_in_the_session(self):
        response = self.client.post(
            reverse("calendar_prefs"), {"weekends": "0", "slot": "01:00:00"}
        )
        self.assertEqual(response.json()["weekends"], False)
        self.assertEqual(response.json()["slot"], "01:00:00")
        # The next panel render reflects them.
        html = self.client.get(
            reverse("crm_panel", args=["mi-calendario"])
        ).content.decode()
        self.assertIn('data-weekends="0"', html)
        self.assertIn('data-slot="01:00:00"', html)

    def test_unknown_slot_falls_back(self):
        response = self.client.post(
            reverse("calendar_prefs"), {"weekends": "1", "slot": "00:07:00"}
        )
        self.assertEqual(response.json()["slot"], "00:30:00")
