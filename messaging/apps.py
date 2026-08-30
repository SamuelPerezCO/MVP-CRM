from django.apps import AppConfig


class MessagingConfig(AppConfig):
    """The messaging layer: conversations, messages and provider integrations.

    Deliberately separate from ``core`` (the UI shell): everything that talks
    to WhatsApp/Meta/Twilio lives here, so swapping or adding a provider never
    touches a view or template.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "messaging"
    verbose_name = "mensajería"
