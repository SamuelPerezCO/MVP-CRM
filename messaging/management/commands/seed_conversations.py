"""Seed the Inbox with realistic demo conversations.

Everything created here is obviously fake: surnames are "Pruebas"/"Demo"/
"Ejemplo" and phones live in the reserved-looking +5730000000xx range --
never real personal data. Conversations are spread across channels, across
assignment states (Tu inbox / Sin asignar) and across the 24-hour boundary,
so both composer states and every nav filter have something to show.

Idempotent-ish: run with --fresh to wipe previous seed data (recognized by
the phone prefix) before recreating it.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.calendario import CALENDAR_TZ
from core.models import CalendarEvent, Client
from messaging import services
from messaging.models import Conversation, Message, Tag

#: All seeded contacts share this prefix -- it is what --fresh deletes by.
FAKE_PHONE_PREFIX = "+5730000000"

#: Demo advisor whose "Tu inbox" gets conversations. Password printed on
#: creation so /admin login works out of the box.
DEMO_USERNAME = "asesor"
DEMO_PASSWORD = "asesor123"

IN, OUT = Message.INBOUND, Message.OUTBOUND

#: The demo tag set, matching the Mercately-style reference. get_or_create'd
#: by name, so re-running never duplicates and user edits survive.
TAG_DEFS = [
    ("CLIENTE NUEVO", "yellow"),
    ("VENTA EFECTIVA", "green"),
    ("PRIMER CONTACTO", "yellow"),
    ("SHOPIFY NUEVO", "purple"),
    ("MAYORISTA EFECTIVA", "blue"),
]

# One entry per conversation. ``hours_ago`` is when the exchange starts;
# scripts inside the 24h window keep the composer live, the older ones show
# the template-required notice. ``script`` rows are (direction, minutes after
# start, text).
SEED = [
    {
        "first_name": "Camila", "last_name": "Pruebas", "phone": "01",
        "channel": "whatsapp", "assign": "demo", "status": "open",
        "hours_ago": 2, "unread": 2,
        "tags": ['CLIENTE NUEVO', 'PRIMER CONTACTO'],
        "script": [
            (IN,  0,  "Hola! Vi la promoción de las camisetas en Instagram, ¿todavía está disponible?"),
            (OUT, 3,  "¡Hola Camila! Claro que sí, la promo 2x1 va hasta el domingo 🙌"),
            (IN,  6,  "Genial. ¿Tienen la blanca en talla M?"),
            (OUT, 9,  "Sí, nos quedan 4 en talla M. ¿Te la aparto?"),
            (IN,  15, "Sí porfa, ¿puedo pagar contra entrega?"),
            (IN,  16, "Y otra cosa, ¿hacen envíos a Envigado?"),
        ],
    },
    {
        "first_name": "Andrés", "last_name": "Demo", "phone": "02",
        "channel": "whatsapp", "assign": None, "status": "pending",
        "hours_ago": 5, "unread": 1,
        "tags": ['VENTA EFECTIVA'],
        "script": [
            (IN,  0,  "Buenas tardes, hice un pedido el lunes y aún no me llega el número de guía"),
            (OUT, 10, "Hola Andrés, una disculpa. ¿Me confirmas el número de pedido?"),
            (IN,  14, "Es el #10432"),
            (OUT, 20, "Listo, lo reviso con la transportadora y te escribo hoy mismo."),
            (IN,  180, "¿Alguna novedad? 🙏"),
        ],
    },
    {
        "first_name": "Valentina", "last_name": "Ejemplo", "phone": "03",
        "channel": "instagram-dm", "assign": "demo", "status": "open",
        "hours_ago": 8, "unread": 0,
        "tags": ['CLIENTE NUEVO', 'SHOPIFY NUEVO'],
        "script": [
            (IN,  0,  "Holaa, ¿el bolso café que subieron a historias tiene garantía?"),
            (OUT, 4,  "¡Hola Valentina! Sí, 6 meses por defectos de fábrica ✨"),
            (IN,  9,  "Perfecto, ¿cuánto cuesta con envío a Bogotá?"),
            (OUT, 12, "Queda en $145.000 con envío incluido. ¿Te lo empacamos?"),
            (IN,  30, "Déjame lo pienso y te escribo mañana, gracias!"),
            (OUT, 32, "¡Con gusto! Aquí estamos 😊"),
        ],
    },
    {
        "first_name": "Santiago", "last_name": "Ficticio", "phone": "04",
        "channel": "messenger", "assign": None, "status": "open",
        "hours_ago": 20, "unread": 1,
        "tags": ['PRIMER CONTACTO'],
        "script": [
            (IN,  0,  "Hola, ¿tienen tienda física en Medellín o solo venden en línea?"),
            (OUT, 25, "¡Hola Santiago! Por ahora solo en línea, pero el envío en Medellín llega en 24h 🚚"),
            (IN,  40, "Ah perfecto. ¿Y aceptan Nequi?"),
        ],
    },
    # --- Outside the 24h window: the composer must show the template notice.
    {
        "first_name": "Mariana", "last_name": "Simulada", "phone": "05",
        "channel": "whatsapp", "assign": "demo", "status": "pending",
        "hours_ago": 30, "unread": 0,
        "tags": ['VENTA EFECTIVA', 'MAYORISTA EFECTIVA'],
        "script": [
            (IN,  0,  "Hola, quiero cambiar la talla de un pantalón que compré la semana pasada"),
            (OUT, 8,  "Hola Mariana, claro. ¿Me envías una foto de la etiqueta del pedido?"),
            (IN,  20, "Aquí está, es el pedido #10395"),
            (OUT, 26, "Recibido. Te agendo la recogida para mañana entre 9am y 12m, ¿te sirve?"),
            (IN,  35, "Sí, perfecto. Quedo pendiente"),
        ],
    },
    {
        "first_name": "Julián", "last_name": "Prueba", "phone": "06",
        "channel": "whatsapp", "assign": None, "status": "resolved",
        "hours_ago": 72, "unread": 0,
        "tags": ['VENTA EFECTIVA'],
        "script": [
            (IN,  0,  "Buenos días, ¿el combo de vasos incluye los pitillos?"),
            (OUT, 12, "¡Buenos días Julián! Sí, incluye 4 pitillos de acero 🥤"),
            (IN,  18, "Listo, acabo de hacer el pedido por la página"),
            (OUT, 22, "¡Mil gracias por tu compra! Te llega el número de guía por aquí."),
            (IN,  400, "Llegó todo perfecto, gracias!"),
            (OUT, 410, "¡Qué alegría! Cualquier cosa nos escribes 🙌"),
        ],
    },
    {
        "first_name": "Daniela", "last_name": "Demo", "phone": "07",
        "channel": "facebook", "assign": None, "status": "open",
        "hours_ago": 50, "unread": 0,
        "tags": ['MAYORISTA EFECTIVA', 'PRIMER CONTACTO'],
        "script": [
            (IN,  0,  "Hola, ¿hacen ventas al por mayor? Tengo una tienda en Bucaramanga"),
            (OUT, 30, "¡Hola Daniela! Sí, a partir de 20 unidades hay precio mayorista. Te comparto el catálogo."),
            (IN,  90, "Súper, quedo atenta al catálogo"),
        ],
    },
]


#: A week of Mi calendario entries at Bogotá business hours: (weekday with
#: 0=Monday, start "HH:MM", duration in minutes, event type, title, phone
#: suffix of the seeded contact to link -- or None). Titles double as the
#: --fresh deletion key, so keep them distinctive.
EVENT_SEED = [
    (0, "09:00", 30, "llamada", "Llamada de bienvenida a Camila", "01"),
    (0, "15:00", 60, "reunion", "Reunión semanal del equipo", None),
    (1, "10:30", 30, "seguimiento", "Seguimiento pedido #10432", "02"),
    (2, "08:00", 45, "llamada", "Llamada mayorista con Daniela", "07"),
    (2, "14:00", 60, "reunion", "Demo de catálogo para Valentina", "03"),
    (3, "11:00", 30, "seguimiento", "Confirmar recogida del cambio de talla", "05"),
    (4, "09:30", 90, "reunion", "Planeación de campaña de septiembre", None),
    (4, "16:00", 30, "otro", "Cierre de caja de la semana", None),
]

SEED_EVENT_TITLES = [row[4] for row in EVENT_SEED]

#: Marks seeded events so --fresh never deletes a user-created event that
#: happens to share a title.
SEED_EVENT_DESCRIPTION = "Evento de demostración."


# --- Volume backdrop (Estadísticas > Volumen de Mensajes) --------------------
#
# The seven conversations above are written to be *read* -- they carry the
# Inbox. They are also far too few to draw a chart: 35 messages over a month
# is a flat line. So the stats screens get a second, separate population:
# high-volume filler conversations whose message bodies nobody opens, but
# whose timestamps carry a believable rhythm.
#
# Three rhythms are layered, because all three are visible on the screen:
#   * per weekday  -- the line dips every Saturday and Sunday
#   * per hour     -- the HORA PICO tile needs a real mid-morning peak
#   * per channel  -- three lines at different altitudes, not one tripled
#
# Deterministic (SEED_RNG below), so re-seeding redraws the same chart and a
# screenshot stays comparable across runs.

VOLUME_PHONE_PREFIX = FAKE_PHONE_PREFIX + "9"

#: Chart channel -> (Conversation.channel, contacts to spread across,
#: average messages on a full weekday). The averages set the three lines'
#: altitudes; their sum x ~30 days is the order of magnitude the KPI tiles
#: show.
VOLUME_CHANNELS = [
    ("whatsapp", 4, 780),
    ("messenger", 3, 380),
    ("instagram-dm", 3, 210),
]

#: Monday=0 .. Sunday=6. Business traffic, so the weekend is a real dip and
#: not just noise; Friday tails off slightly.
VOLUME_WEEKDAY_FACTOR = [1.00, 1.06, 1.04, 1.00, 0.88, 0.54, 0.33]

#: Relative weight per hour of the day (index = hour, REPORT_TZ wall clock).
#: Shaped like a support desk: dead overnight, a ramp from 7, the peak in
#: the 10-11 band the HORA PICO tile names, a lunch dip, a smaller
#: afternoon bump, then a taper.
VOLUME_HOUR_WEIGHTS = [
    0.4, 0.2, 0.1, 0.1, 0.1, 0.3, 1.0, 2.6,   # 00-07
    5.2, 7.8, 10.0, 8.6, 6.4, 4.8, 5.6, 6.8,  # 08-15
    7.2, 6.6, 5.4, 3.8, 2.6, 1.6, 1.0, 0.6,   # 16-23
]

#: Share of messages that are inbound. The reference's tiles read 37% /
#: 63% -- a support inbox answers more than it is asked.
VOLUME_INBOUND_SHARE = 0.37

#: Fixed so the seeded chart is reproducible.
VOLUME_RNG_SEED = 20260828

#: Bodies are filler -- these conversations exist for their timestamps. Kept
#: obviously synthetic so a stats row can never be mistaken for real traffic.
VOLUME_BODY = "Mensaje de relleno para estadísticas."


class Command(BaseCommand):
    help = "Create fake Colombian contacts and WhatsApp-style conversations for the Inbox."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Delete previously seeded conversations/contacts first.",
        )
        parser.add_argument(
            "--volume-days",
            type=int,
            default=30,
            help=(
                "Days of message-volume backdrop for the Estadísticas charts "
                "(default 30, matching the period picker's default window). "
                "0 skips it."
            ),
        )
        parser.add_argument(
            "--volume-scale",
            type=float,
            default=1.0,
            help=(
                "Multiplier on the volume backdrop's daily averages. Lower it "
                "(e.g. 0.1) for a faster seed on a slow machine; the chart "
                "keeps its shape, only the axis shrinks."
            ),
        )

    def handle(self, *args, **options):
        if options["fresh"]:
            deleted, _ = Client.objects.filter(
                phone__startswith=FAKE_PHONE_PREFIX
            ).delete()  # conversations/messages cascade
            # Calendar events don't cascade (contact is SET_NULL): delete by
            # their seed titles.
            events_deleted, _ = CalendarEvent.objects.filter(
                title__in=SEED_EVENT_TITLES,
                description=SEED_EVENT_DESCRIPTION,
            ).delete()
            self.stdout.write(
                f"Removed {deleted + events_deleted} previously seeded rows."
            )

        demo_user = self._demo_user()
        tags = self._demo_tags(demo_user)
        now = timezone.now()
        created = 0

        for spec in SEED:
            phone = FAKE_PHONE_PREFIX + spec["phone"]
            contact, _ = Client.objects.get_or_create(
                phone=phone,
                defaults={
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "channel": "whatsapp" if spec["channel"] == "whatsapp" else
                    ("instagram" if spec["channel"] == "instagram-dm" else spec["channel"]),
                    "country": "CO",
                },
            )
            if contact.conversations.exists():
                continue  # already seeded; --fresh to rebuild

            conversation = Conversation.objects.create(
                contact=contact,
                channel=spec["channel"],
                status=spec["status"],
                assigned_to=demo_user if spec["assign"] == "demo" else None,
                unread_count=spec["unread"],
            )

            start = now - timedelta(hours=spec["hours_ago"])
            last_at = last_inbound_at = None
            for direction, minutes, text in spec["script"]:
                stamp = start + timedelta(minutes=minutes)
                Message.objects.create(
                    conversation=conversation,
                    direction=direction,
                    body=text,
                    # Outbound history reads as read; inbound is delivered by
                    # definition. Fresh sends from the UI start at queued and
                    # advance via the fake provider instead.
                    status="read" if direction == OUT else "delivered",
                    provider_message_id=f"seed-{uuid.uuid4().hex}",
                    timestamp=stamp,
                    sent_by=demo_user if direction == OUT else None,
                )
                last_at = stamp
                if direction == IN:
                    last_inbound_at = stamp

            conversation.last_message_at = last_at
            conversation.last_inbound_at = last_inbound_at
            conversation.save(update_fields=["last_message_at", "last_inbound_at"])

            # Tag through the same service the UI uses, so tagged_by lands.
            for tag_name in spec.get("tags", []):
                services.apply_tag([conversation], tags[tag_name], demo_user)

            created += 1

        events_created = self._seed_events(demo_user)
        volume_created = self._seed_volume(
            demo_user, options["volume_days"], options["volume_scale"]
        )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {created} conversations and {events_created} calendar "
            f"events ({Conversation.objects.count()} conversations total)."
        ))
        if volume_created:
            self.stdout.write(self.style.SUCCESS(
                f"Seeded {volume_created} backdrop messages for the "
                f"Estadísticas charts."
            ))
        self.stdout.write(
            f"'Tu inbox' works after logging into /admin as "
            f"{DEMO_USERNAME!r} / {DEMO_PASSWORD!r}."
        )

    def _seed_events(self, demo_user) -> int:
        """A week of Mi calendario entries at Bogotá business hours, some
        linked to the seeded contacts. Idempotent by title -- an existing
        title is left untouched, so re-runs never duplicate."""
        now_bogota = timezone.now().astimezone(CALENDAR_TZ)
        monday = (now_bogota - timedelta(days=now_bogota.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        created = 0
        for weekday, start_hm, minutes, event_type, title, suffix in EVENT_SEED:
            if CalendarEvent.objects.filter(
                title=title, description=SEED_EVENT_DESCRIPTION
            ).exists():
                continue
            hour, minute = (int(part) for part in start_hm.split(":"))
            start = monday + timedelta(days=weekday, hours=hour, minutes=minute)
            contact = (
                Client.objects.filter(phone=FAKE_PHONE_PREFIX + suffix).first()
                if suffix
                else None
            )
            CalendarEvent.objects.create(
                title=title,
                description=SEED_EVENT_DESCRIPTION,
                start=start,
                end=start + timedelta(minutes=minutes),
                event_type=event_type,
                contact=contact,
                assigned_to=demo_user,
                created_by=demo_user,
            )
            created += 1
        return created

    def _seed_volume(self, demo_user, days: int, scale: float) -> int:
        """Message-volume backdrop for the Estadísticas charts.

        Walks the last ``days`` days in REPORT_TZ and, for each channel,
        turns a weekday factor and a 24-slot hourly curve into individual
        Message rows -- the aggregation being charted is a plain GROUP BY,
        so the rhythm has to be in the rows, not faked at read time.

        Idempotent by contact: an existing volume conversation means the
        backdrop is already there, so a re-run is a no-op (--fresh rebuilds).
        Rows go in via bulk_create, which is the difference between seconds
        and minutes at ~40k messages.
        """
        if days <= 0:
            return 0
        if Client.objects.filter(phone__startswith=VOLUME_PHONE_PREFIX).exists():
            self.stdout.write("Volume backdrop already present; --fresh to rebuild.")
            return 0

        rng = random.Random(VOLUME_RNG_SEED)
        # Bucket by the same wall clock the report groups by, so the peak
        # lands in the hour the tile names.
        today = timezone.now().astimezone(CALENDAR_TZ).date()
        hour_total = sum(VOLUME_HOUR_WEIGHTS)

        messages = []
        # conversation id -> newest timestamp, so the Inbox list's sort key
        # stays truthful for these rows too.
        last_seen = {}
        contact_seq = 0

        for channel, contact_count, weekday_average in VOLUME_CHANNELS:
            conversations = []
            for _ in range(contact_count):
                contact_seq += 1
                contact = Client.objects.create(
                    first_name="Volumen",
                    last_name=f"Estadísticas {contact_seq:02d}",
                    phone=f"{VOLUME_PHONE_PREFIX}{contact_seq:02d}",
                    channel="instagram" if channel == "instagram-dm" else channel,
                    country="CO",
                )
                conversations.append(
                    Conversation.objects.create(
                        contact=contact,
                        channel=channel,
                        status=Conversation.RESOLVED,
                        assigned_to=demo_user,
                    )
                )

            for offset in range(days):
                day = today - timedelta(days=days - 1 - offset)
                # Weekday rhythm, a gentle upward trend across the period so
                # the three lines aren't parallel, and per-day noise.
                trend = 0.86 + 0.28 * (offset / max(days - 1, 1))
                jitter = rng.uniform(0.88, 1.12)
                daily = (
                    weekday_average
                    * VOLUME_WEEKDAY_FACTOR[day.weekday()]
                    * trend
                    * jitter
                    * scale
                )

                for hour, weight in enumerate(VOLUME_HOUR_WEIGHTS):
                    count = int(round(daily * weight / hour_total))
                    for _ in range(count):
                        stamp = datetime(
                            day.year, day.month, day.day, hour,
                            rng.randrange(60), rng.randrange(60),
                            tzinfo=CALENDAR_TZ,
                        )
                        conversation = rng.choice(conversations)
                        inbound = rng.random() < VOLUME_INBOUND_SHARE
                        messages.append(Message(
                            conversation=conversation,
                            direction=IN if inbound else OUT,
                            body=VOLUME_BODY,
                            status="delivered" if inbound else "read",
                            provider_message_id=f"vol-{uuid.uuid4().hex}",
                            timestamp=stamp,
                            sent_by=None if inbound else demo_user,
                        ))
                        previous = last_seen.get(conversation.pk)
                        if previous is None or stamp > previous:
                            last_seen[conversation.pk] = stamp

        Message.objects.bulk_create(messages, batch_size=2000)
        for conversation_id, stamp in last_seen.items():
            Conversation.objects.filter(pk=conversation_id).update(
                last_message_at=stamp, last_inbound_at=stamp
            )
        return len(messages)

    def _demo_tags(self, demo_user) -> dict[str, Tag]:
        """The demo tag set, keyed by name. Existing tags (matched
        case-insensitively) are reused untouched, so a recolor in the
        Etiquetas page survives a re-seed."""
        tags = {}
        for name, color in TAG_DEFS:
            tag = Tag.objects.filter(name__iexact=name).first()
            if tag is None:
                tag = Tag.objects.create(name=name, color=color, created_by=demo_user)
            tags[name] = tag
        return tags

    def _demo_user(self):
        """The 'asesor' user some conversations are assigned to."""
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=DEMO_USERNAME,
            defaults={"first_name": "Asesor", "last_name": "Demo", "is_staff": True},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
        return user
