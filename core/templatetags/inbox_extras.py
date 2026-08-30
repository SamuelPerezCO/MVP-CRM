"""Small template helpers for the Inbox screen."""

from datetime import timedelta

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def dict_get(mapping, key):
    """Look up ``mapping[key]`` from a template, where the key is a variable.

    Django's dot lookup can't index a dict by a variable, and the per-channel
    counts are keyed by filter key -- so ``counts|dict_get:row.key``.
    """
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None


@register.filter
def relative_es(value):
    """A Spanish relative age for conversation rows: "unos segundos",
    "un minuto", "7 minutos", "2 horas", "3 días".

    A tiny purpose-built helper instead of ``timesince``: the list wants
    exactly one coarse unit with natural Spanish ("unos segundos", not
    "0 minutos"), and no locale machinery.
    """
    if not value:
        return ""
    seconds = (timezone.now() - value).total_seconds()
    if seconds < 60:
        return "unos segundos"
    minutes = int(seconds // 60)
    if minutes < 60:
        return "un minuto" if minutes == 1 else f"{minutes} minutos"
    hours = int(seconds // 3600)
    if hours < 24:
        return "una hora" if hours == 1 else f"{hours} horas"
    days = int(seconds // 86400)
    if days < 30:
        return "un día" if days == 1 else f"{days} días"
    months = days // 30
    return "un mes" if months == 1 else f"{months} meses"


@register.filter
def chat_stamp(value):
    """A WhatsApp-style short timestamp: time today, "Ayer", then a date.

    Chat UIs care about recency, not precision -- "14:32" says more than a
    full date for today's messages, and anything older than yesterday only
    needs the day.
    """
    if not value:
        return ""
    local = timezone.localtime(value)
    today = timezone.localdate()
    if local.date() == today:
        return local.strftime("%H:%M")
    if local.date() == today - timedelta(days=1):
        return "Ayer"
    return local.strftime("%d/%m/%y")
