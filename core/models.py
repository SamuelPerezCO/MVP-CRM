"""Domain models for the CRM."""

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
