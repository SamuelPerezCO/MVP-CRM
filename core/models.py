"""Domain models for the CRM."""

from django.conf import settings
from django.db import models


class Client(models.Model):
    """A person in the CRM's client list.

    Fields map directly onto the Clientes table columns: name, phone (with the
    country driving a flag), mail and canal. WhatsApp availability is derived
    from ``channel`` rather than stored separately, so the two can't disagree.
    """

    # Channel keys deliberately match core.inbox.CANALES so a client's channel
    # and an inbox conversation filter refer to the same thing.
    CHANNEL_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("messenger", "Messenger"),
        ("instagram", "Instagram"),
        ("facebook", "Facebook"),
        ("tiktok", "TikTok"),
    ]

    first_name = models.CharField("nombres", max_length=80)
    last_name = models.CharField("apellidos", max_length=80, blank=True)

    phone = models.CharField("teléfono", max_length=20, help_text="E.164, e.g. +573167687288")
    country = models.CharField(
        "país", max_length=2, blank=True, help_text="ISO 3166-1 alpha-2, drives the flag"
    )

    email = models.EmailField("mail", blank=True)
    channel = models.CharField("canal", max_length=20, choices=CHANNEL_CHOICES, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["first_name", "last_name"]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        """Up to two letters for the Inbox's avatar circles."""
        letters = f"{self.first_name[:1]}{self.last_name[:1]}".upper()
        return letters or "?"

    @property
    def has_whatsapp(self) -> bool:
        """Whether to offer the green 'Iniciar conversación' link on this row."""
        return self.channel == "whatsapp"

    @property
    def flag(self) -> str:
        """The country as a flag emoji, built from regional indicator symbols.

        Avoids shipping flag images; returns "" for a missing or malformed code
        so the template can just print it.
        """
        code = (self.country or "").upper()
        if len(code) != 2 or not code.isalpha():
            return ""
        return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)

    @property
    def whatsapp_url(self) -> str:
        """wa.me link for the phone number, digits only."""
        digits = "".join(char for char in self.phone if char.isdigit())
        return f"https://wa.me/{digits}"


class Product(models.Model):
    """A product in the Mi comercio catalogue.

    Fields map directly onto the Productos table columns. Categoría and Marca
    are plain text for now -- they become foreign keys once the Categorías and
    Marcas pages define real models. "Sincronizado con" (the sales channels a
    product is synced to) deliberately has *no* field yet: it will be an M2M to
    a SalesChannel model once channel integrations exist, and the table renders
    an empty cell until then.
    """

    # The Productos tabs filter by these values; the tab-slug -> status
    # mapping lives in core.comercio._TAB_STATUS, next to the tab list itself
    # (the slugs are plural -- "activos" -- while these keys are singular).
    STATUS_CHOICES = [
        ("activo", "Activo"),
        ("inactivo", "Inactivo"),
    ]

    name = models.CharField("nombre", max_length=120)
    stock = models.PositiveIntegerField("stock", default=0)
    price = models.DecimalField("precio", max_digits=10, decimal_places=2)

    category = models.CharField("categoría", max_length=80, blank=True)
    brand = models.CharField("marca", max_length=80, blank=True)

    status = models.CharField(
        "estado", max_length=10, choices=STATUS_CHOICES, default="activo"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "producto"
        verbose_name_plural = "productos"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ClientList(models.Model):
    """A named group of clients -- one row in the "Lista de clientes" table.

    "Número de contactos" is derived from the ``clients`` M2M (annotated in
    the view) so the count can never disagree with the list's actual members.
    ``created_by`` is plain text until the app grows real users/auth, at which
    point it becomes a foreign key.
    """

    name = models.CharField("nombre del grupo", max_length=120)
    clients = models.ManyToManyField(
        Client, related_name="client_lists", blank=True, verbose_name="clientes"
    )

    created_by = models.CharField("creado por", max_length=80, blank=True)
    created_at = models.DateTimeField("fecha", auto_now_add=True)

    class Meta:
        verbose_name = "lista de clientes"
        verbose_name_plural = "listas de clientes"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class MessageTemplate(models.Model):
    """A WhatsApp message template -- one row in the Plantillas table and the
    product of the Crear plantilla editor.

    ``is_active`` (Activo) is the account's own on/off toggle; ``status``
    (Estado) is the WhatsApp approval verdict -- separate fields because they
    move independently: an approved template can be switched off. The
    Desactivadas tab filters on the toggle, the other tabs on the status.

    The choice lists here are flat unions so stored values always display;
    which sub-types each *category* actually offers, the language list and
    the editor's validation all live in core.plantillas. ``team`` and
    ``created_by`` stay plain text until the app grows real users/auth (same
    stance as ClientList.created_by).
    """

    CATEGORY_CHOICES = [
        ("marketing", "Marketing"),
        ("utility", "Utility"),
        ("authentication", "Autenticación"),
    ]

    SUB_TYPE_CHOICES = [
        ("custom", "Mensaje personalizado"),
        ("limited_time_offer", "Oferta de tiempo limitado"),
        ("carousel", "Carrusel"),
        ("auth_code", "Código de autenticación"),
    ]

    HEADER_CHOICES = [
        ("none", "Ninguno"),
        ("text", "Texto"),
        ("image", "Imagen"),
        ("video", "Video"),
        ("document", "Documento"),
    ]

    STATUS_CHOICES = [
        ("pendiente", "Pendiente"),
        ("aceptada", "Aceptada"),
        ("rechazada", "Rechazada"),
    ]

    # Meta constraint, not a style choice: lowercase, digits and _ only.
    # The regex itself lives in core.plantillas.NAME_RE (single source).
    name = models.CharField("nombre", max_length=120)
    category = models.CharField(
        "categoría", max_length=20, choices=CATEGORY_CHOICES, default="marketing"
    )
    sub_type = models.CharField(
        "tipo", max_length=30, choices=SUB_TYPE_CHOICES, default="custom"
    )
    language = models.CharField("idioma", max_length=10, default="es")
    team = models.CharField("equipo", max_length=80, blank=True)

    header_type = models.CharField(
        "cabecera", max_length=10, choices=HEADER_CHOICES, default="none"
    )
    header_text = models.CharField("texto de cabecera", max_length=60, blank=True)
    header_media = models.FileField(
        "archivo de cabecera", upload_to="plantillas/", blank=True
    )

    body = models.TextField("cuerpo", blank=True)
    #: One sample string per {{n}} variable, element i pairing with {{i+1}} --
    #: Meta requires example values at submission time and the preview
    #: substitutes them live.
    body_sample_values = models.JSONField(
        "valores de ejemplo", default=list, blank=True
    )
    footer = models.CharField("pie de página", max_length=60, blank=True)
    #: List of {"type": "quick_reply"|"url"|"phone", "text": ..., ...} dicts.
    buttons = models.JSONField("botones", default=list, blank=True)

    is_active = models.BooleanField("activo", default=True)
    status = models.CharField(
        "estado", max_length=10, choices=STATUS_CHOICES, default="pendiente"
    )
    rejection_reason = models.TextField("motivo de rechazo", blank=True)

    #: The id Meta assigned when the plantilla was submitted for approval.
    #: Blank means it was never submitted (no META_WABA_ID at save time, or
    #: the submission failed) -- the status sync matches by (name, language)
    #: anyway, so a template created in Meta's own console still reconciles.
    provider_template_id = models.CharField(
        "id en el proveedor", max_length=64, blank=True
    )
    #: When the approval state was last read back from the provider. Null
    #: until the first sync; the Plantillas page shows it so "Pendiente"
    #: reads as "pending as of <when>", not as a guess.
    status_synced_at = models.DateTimeField(
        "estado sincronizado", null=True, blank=True
    )

    created_by = models.CharField("creado por", max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "plantilla de WhatsApp"
        verbose_name_plural = "plantillas de WhatsApp"
        ordering = ["name"]
        constraints = [
            # Meta scopes template names per language, so the pair is the key.
            models.UniqueConstraint(
                fields=["name", "language"], name="unique_template_name_per_language"
            ),
        ]

    def __str__(self) -> str:
        return self.name

class QuickReply(models.Model):
    """A canned answer the composer's Respuestas rápidas picker sends in one
    click -- the account's own, kept apart from WhatsApp plantillas.

    A plantilla (:class:`MessageTemplate`) is Meta's concept: approved text
    with numbered variables, the only thing allowed outside the 24h window.
    A quick reply is the team's: free text written once ("Nuestro horario es
    de 9 a 6"), optionally with an image (a price list, the store front),
    sent inside the window like any typed message. The picker used to list
    plantillas because nothing else existed; now it lists these, and
    plantillas keep their real job in the "Enviar plantilla" flow.

    ``image`` goes through default storage (Vercel Blob in production), so
    the URL the provider is handed is public -- Meta fetches it by link.
    """

    title = models.CharField("título", max_length=80)
    body = models.TextField("texto", blank=True)
    # FileField, not ImageField: the latter needs Pillow, which this project
    # doesn't ship (MessageTemplate.header_media makes the same call). The
    # form restricts uploads to image types instead.
    image = models.FileField("imagen", upload_to="respuestas/", blank=True)

    #: Off means hidden from the picker without deleting the text.
    is_active = models.BooleanField("activa", default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quick_replies_created",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "respuesta rápida"
        verbose_name_plural = "respuestas rápidas"
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title

    @property
    def has_image(self) -> bool:
        return bool(self.image)


class CalendarEvent(models.Model):
    """One entry in the CRM's Mi calendario.

    ``contact`` is the reason a calendar lives inside a CRM: an event links
    to a client ("llamada con Camila") so the record is one click away. The
    user FKs are nullable following the Conversation precedent -- the app
    has no real login yet, so events created from the UI carry no user.

    Times are stored in UTC (USE_TZ); entry and display happen in
    core.calendario.CALENDAR_TZ. ``event_type`` picks the color, reusing a
    tag palette pair -- see core.calendario.EVENT_TYPES.
    """

    TYPE_CHOICES = [
        ("llamada", "Llamada"),
        ("reunion", "Reunión"),
        ("seguimiento", "Seguimiento"),
        ("otro", "Otro"),
    ]

    title = models.CharField("título", max_length=120)
    description = models.TextField("descripción", blank=True)

    start = models.DateTimeField("inicio")
    end = models.DateTimeField("fin")
    #: All-day events span whole days: start at midnight, end exclusive.
    all_day = models.BooleanField("todo el día", default=False)

    contact = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendar_events",
        verbose_name="cliente",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="calendar_events",
        verbose_name="asignado a",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_calendar_events",
        verbose_name="creado por",
    )

    event_type = models.CharField(
        "tipo", max_length=20, choices=TYPE_CHOICES, default="reunion"
    )
    reminder_minutes_before = models.PositiveIntegerField(
        "recordatorio (minutos antes)", null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "evento de calendario"
        verbose_name_plural = "eventos de calendario"
        ordering = ["start"]
        indexes = [
            # The grid's query: events in a window, per advisor.
            models.Index(fields=["start", "assigned_to"], name="calendar_start_advisor_idx"),
        ]

    def __str__(self) -> str:
        return self.title
