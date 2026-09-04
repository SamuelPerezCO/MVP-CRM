"""The rules behind the CRM's Clientes CRUD: phone normalization, the country
list and form validation.

Kept out of the view for the same reason :mod:`core.plantillas` is: what a
usable phone number looks like is domain knowledge, not request handling, and
the Inbox depends on it too. ``messaging.services._upsert_contact`` finds the
Client for an inbound message by an exact ``phone`` match, so a number typed
with spaces here ("+57 316 768 7288") would create a *second* client the next
time that person writes. :func:`normalize_phone` is what keeps the two sides
speaking the same string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Client

#: Digits allowed in an E.164 number, country code included. The ITU caps it
#: at 15; the floor is deliberately loose -- short national formats exist and
#: rejecting a real customer's number is worse than storing an odd one.
PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Country:
    """One entry in the País picker."""

    code: str
    """ISO 3166-1 alpha-2 -- what ``Client.country`` stores and the flag needs."""

    name: str
    """Spanish name, for the dropdown."""

    dialing: str
    """International prefix, used to guess the country from a typed number."""


#: The picker's options, sorted by name (Spanish collation is close enough to
#: ASCII order for these). Not an exhaustive ISO list on purpose: it covers the
#: markets this CRM is for, and ``Client.country`` is a free CharField, so a
#: country that arrives from a webhook still stores and still gets its flag.
COUNTRIES = [
    Country("DE", "Alemania", "+49"),
    Country("AR", "Argentina", "+54"),
    Country("BO", "Bolivia", "+591"),
    Country("BR", "Brasil", "+55"),
    Country("CA", "Canadá", "+1"),
    Country("CL", "Chile", "+56"),
    Country("CO", "Colombia", "+57"),
    Country("CR", "Costa Rica", "+506"),
    Country("CU", "Cuba", "+53"),
    Country("EC", "Ecuador", "+593"),
    Country("SV", "El Salvador", "+503"),
    Country("ES", "España", "+34"),
    Country("US", "Estados Unidos", "+1"),
    Country("FR", "Francia", "+33"),
    Country("GT", "Guatemala", "+502"),
    Country("HN", "Honduras", "+504"),
    Country("IT", "Italia", "+39"),
    Country("MX", "México", "+52"),
    Country("NI", "Nicaragua", "+505"),
    Country("PA", "Panamá", "+507"),
    Country("PY", "Paraguay", "+595"),
    Country("PE", "Perú", "+51"),
    Country("PT", "Portugal", "+351"),
    Country("GB", "Reino Unido", "+44"),
    Country("DO", "República Dominicana", "+1809"),
    Country("UY", "Uruguay", "+598"),
    Country("VE", "Venezuela", "+58"),
]

COUNTRY_CODES = {country.code for country in COUNTRIES}

#: Longest prefix first, so +1809 (Dominican Republic) is matched before +1.
#: Same-length ties keep COUNTRIES order, which is why +1 lands on Canada
#: rather than the US -- a coin flip either way, and the agent can override it
#: in the picker. This only ever fills a *blank* country.
_BY_DIALING = sorted(COUNTRIES, key=lambda c: -len(c.dialing))


def normalize_phone(raw: str) -> str:
    """Squash a typed number down to ``+`` followed by digits.

    Everything humans put in phone numbers -- spaces, dashes, parentheses,
    dots -- comes out; a missing ``+`` is added, since every number this CRM
    stores is international (the channels it talks to have no concept of a
    local one). Returns "" for a string with no digits at all, which
    :func:`validate` then rejects.
    """
    digits = re.sub(r"\D", "", raw or "")
    return f"+{digits}" if digits else ""


def country_from_phone(phone: str) -> str:
    """Best-effort ISO code for a normalized number, or "" if none matches.

    Only used to fill a country the agent left blank -- an explicit choice is
    never second-guessed.
    """
    for country in _BY_DIALING:
        if phone.startswith(country.dialing):
            return country.code
    return ""


def form_state(post=None, client=None) -> dict:
    """The dialog's field values: what was posted, else an existing client's,
    else blanks.

    One dict feeds both the template and :func:`validate`, so a rejected
    submit re-renders showing exactly what the agent typed.
    """
    if post is not None:
        return {
            "first_name": (post.get("first_name") or "").strip(),
            "last_name": (post.get("last_name") or "").strip(),
            "phone": (post.get("phone") or "").strip(),
            "country": (post.get("country") or "").strip().upper(),
            "email": (post.get("email") or "").strip(),
            "channel": (post.get("channel") or "").strip(),
        }
    if client is not None:
        return {
            "first_name": client.first_name,
            "last_name": client.last_name,
            "phone": client.phone,
            "country": client.country,
            "email": client.email,
            "channel": client.channel,
        }
    return {
        "first_name": "",
        "last_name": "",
        "phone": "",
        "country": "",
        "email": "",
        "channel": "",
    }


def validate(state: dict, client=None) -> dict:
    """Field name -> Spanish error message; empty when the state is saveable.

    ``client`` is the row being edited, excluded from the duplicate-phone
    check so saving a client without touching their number isn't rejected as
    a clash with themselves.
    """
    errors: dict[str, str] = {}

    if not state["first_name"]:
        errors["first_name"] = "El nombre es obligatorio."
    elif len(state["first_name"]) > 80:
        errors["first_name"] = "Máximo 80 caracteres."

    if len(state["last_name"]) > 80:
        errors["last_name"] = "Máximo 80 caracteres."

    phone = normalize_phone(state["phone"])
    if not phone:
        errors["phone"] = "El teléfono es obligatorio."
    elif not PHONE_MIN_DIGITS <= len(phone) - 1 <= PHONE_MAX_DIGITS:
        errors["phone"] = (
            f"Escribe el número con indicativo, entre {PHONE_MIN_DIGITS} y "
            f"{PHONE_MAX_DIGITS} dígitos."
        )
    else:
        # Not a database constraint (production data predates this screen and
        # may already hold duplicates), but the Inbox routes inbound messages
        # by an exact phone match, so two clients on one number would split
        # that person's history in two.
        clash = Client.objects.filter(phone=phone)
        if client is not None:
            clash = clash.exclude(pk=client.pk)
        other = clash.first()
        if other is not None:
            errors["phone"] = f"Ese teléfono ya es de {other.full_name}."

    if state["email"] and not EMAIL_RE.match(state["email"]):
        errors["email"] = "Escribe un correo válido."

    if state["country"] and state["country"] not in COUNTRY_CODES:
        errors["country"] = "Elige un país de la lista."

    if state["channel"] and state["channel"] not in dict(Client.CHANNEL_CHOICES):
        errors["channel"] = "Elige un canal de la lista."

    return errors


def apply(state: dict, client=None) -> Client:
    """Create or update the client from a state :func:`validate` accepted."""
    phone = normalize_phone(state["phone"])
    client = client or Client()
    client.first_name = state["first_name"]
    client.last_name = state["last_name"]
    client.phone = phone
    client.country = state["country"] or country_from_phone(phone)
    client.email = state["email"]
    client.channel = state["channel"]
    client.save()
    return client
