"""
Data definition and validation for the Crear plantilla editor: the category
cards, the per-category sub-types, language and header options the form
renders, plus the server-side validation every POST goes through. The client
mirrors these rules live (static/js/shell.js) but this module is the
authority -- nothing reaches the database without passing validate().
"""

import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from messaging.providers.types import TemplateSpec

from .models import MessageTemplate

#: Meta's hard template-name constraint: lowercase, digits and _ only.
NAME_RE = re.compile(r"^[a-z0-9_]+$")

#: A canonical body variable: {{1}}, {{2}}... -- no leading zeros, matching
#: what Meta accepts. shell.js mirrors this exact pattern.
VARIABLE_RE = re.compile(r"\{\{([1-9]\d*)\}\}")

#: A zero-padded almost-variable ({{01}}) -- rejected with guidance instead
#: of silently treated as literal text.
PADDED_VARIABLE_RE = re.compile(r"\{\{0\d*\}\}")

NAME_MAX = 120
TEAM_MAX = 80
BODY_MAX = 1024
HEADER_TEXT_MAX = 60
FOOTER_MAX = 60
QUICK_REPLY_MAX = 3
BUTTON_TEXT_MAX = 25  # Meta's button-label limit.

#: What each media header kind accepts, and a size ceiling for samples.
MEDIA_CONTENT_PREFIXES = {
    "image": "image/",
    "video": "video/",
    "document": "application/pdf",
}
MEDIA_MAX_BYTES = 16 * 1024 * 1024

_CTA_URL_VALIDATOR = URLValidator(schemes=["http", "https"])
PHONE_RE = re.compile(r"^\+?[\d\s().-]{5,20}$")


#: Display labels come from the model's choice lists -- single source, so the
#: table (get_*_display) and the editor can never drift apart.
_CATEGORY_LABELS = dict(MessageTemplate.CATEGORY_CHOICES)
_SUB_TYPE_LABELS = dict(MessageTemplate.SUB_TYPE_CHOICES)
_HEADER_LABELS = dict(MessageTemplate.HEADER_CHOICES)


@dataclass(frozen=True)
class Category:
    """One selectable card in "Elige la categoría"."""

    key: str
    label: str
    icon: str
    description: str

    @property
    def icon_template(self) -> str:
        return f"icons/{self.icon}.svg"


#: Order here is the order rendered, left to right.
CATEGORIES = [
    Category(
        "marketing",
        _CATEGORY_LABELS["marketing"],
        "megaphone",
        "Envía promociones para aumentar el reconocimiento de marca y la interacción.",
    ),
    Category(
        "utility",
        _CATEGORY_LABELS["utility"],
        "package",
        "Envía actualizaciones, alertas y más. Comparte información importante.",
    ),
    Category(
        "authentication",
        _CATEGORY_LABELS["authentication"],
        "lock",
        "Envía códigos que permiten a tus clientes acceder a sus cuentas.",
    ),
]

CATEGORY_BY_KEY = {category.key: category for category in CATEGORIES}
DEFAULT_CATEGORY = "marketing"


@dataclass(frozen=True)
class SubType:
    """One radio row in the per-category sub-type group."""

    key: str
    label: str
    description: str


#: Category key -> the sub-types it offers; the first is that category's
#: default. Utility and Autenticación each carry a single PLACEHOLDER option
#: until their reference screens arrive -- replace those lists, don't extend.
SUB_TYPES = {
    "marketing": [
        SubType(
            "custom",
            _SUB_TYPE_LABELS["custom"],
            "Envía ofertas promocionales, anuncios y mucho más.",
        ),
        SubType(
            "limited_time_offer",
            _SUB_TYPE_LABELS["limited_time_offer"],
            "Muestra fechas de vencimiento y ejecuta temporizadores de cuenta "
            "regresiva para código de oferta.",
        ),
        SubType(
            "carousel",
            _SUB_TYPE_LABELS["carousel"],
            "Envía hasta 10 tarjetas de carousel con imágenes o videos.",
        ),
    ],
    "utility": [
        SubType(
            "custom",
            _SUB_TYPE_LABELS["custom"],
            "Envía actualizaciones, alertas y notificaciones a tus clientes.",
        ),
    ],
    "authentication": [
        SubType(
            "auth_code",
            _SUB_TYPE_LABELS["auth_code"],
            "Envía códigos de verificación de un solo uso.",
        ),
    ],
}


@dataclass(frozen=True)
class Language:
    """One Idioma option: Meta language code + friendly Spanish name."""

    code: str
    label: str


LANGUAGES = [
    Language("es", "Español"),
    Language("es_ES", "Español (España)"),
    Language("es_MX", "Español (México)"),
    Language("es_AR", "Español (Argentina)"),
    Language("en", "Inglés"),
    Language("en_US", "Inglés (EE. UU.)"),
    Language("en_GB", "Inglés (Reino Unido)"),
    Language("pt_BR", "Portugués (Brasil)"),
    Language("pt_PT", "Portugués (Portugal)"),
]

LANGUAGE_BY_CODE = {language.code: language for language in LANGUAGES}
DEFAULT_LANGUAGE = "es"


@dataclass(frozen=True)
class HeaderType:
    """One option in the "Escoge la cabecera del mensaje" segmented control."""

    key: str
    label: str


HEADER_TYPES = [HeaderType(key, _HEADER_LABELS[key]) for key in _HEADER_LABELS]

HEADER_TYPE_KEYS = {header.key for header in HEADER_TYPES}
MEDIA_HEADER_KEYS = {"image", "video", "document"}


@dataclass(frozen=True)
class ButtonKind:
    """One option in the Botones segmented control."""

    key: str
    label: str


BUTTON_KINDS = [
    ButtonKind("none", "Ninguno"),
    ButtonKind("quick", "Respuesta rápida"),
    ButtonKind("cta", "Llamada a la acción"),
]

BUTTON_KIND_KEYS = {kind.key for kind in BUTTON_KINDS}


def team_options() -> list[str]:
    """Distinct team names already stored, for the Equipo dropdown. The empty
    value renders as "Todos los equipos" -- there is no Team model yet, same
    stance as ClientList.created_by."""
    return list(
        MessageTemplate.objects.exclude(team="")
        .order_by("team")
        .values_list("team", flat=True)
        .distinct()
    )


def body_variables(body: str) -> list[int]:
    """The distinct {{n}} numbers in the body, in first-appearance order."""
    numbers = []
    for match in VARIABLE_RE.finditer(body):
        number = int(match.group(1))
        if number not in numbers:
            numbers.append(number)
    return numbers


def render_body(template) -> str:
    """A template's body with each {{n}} replaced by its sample value --
    element i of body_sample_values always pairs with {{i+1}} (see the
    editor's save path below).

    This is what the Inbox's Respuestas rápidas picker drops into the
    composer: the samples make the text sendable as-is, and anything without
    a sample keeps its {{n}} so the agent sees there is a blank to fill
    rather than silently sending a hole.
    """
    samples = template.body_sample_values or []

    def substitute(match) -> str:
        index = int(match.group(1)) - 1
        if 0 <= index < len(samples) and samples[index]:
            return samples[index]
        return match.group(0)

    return VARIABLE_RE.sub(substitute, template.body)


def form_state(post=None) -> dict:
    """The editor's field values: defaults on a fresh GET, the posted values
    (normalized) when re-rendering after errors. Keys mirror the input names.

    Unknown enum values fall back to defaults rather than erroring -- the UI
    can't produce them, and it's the same stance the query params take.
    """
    state = {
        "name": "",
        "category": DEFAULT_CATEGORY,
        "sub_type": SUB_TYPES[DEFAULT_CATEGORY][0].key,
        "language": DEFAULT_LANGUAGE,
        "team": "",
        "header_type": "none",
        "header_text": "",
        "body": "",
        "footer": "",
        "button_kind": "none",
        "quick_replies": ["", "", ""],
        "cta_url_text": "",
        "cta_url": "",
        "cta_phone_text": "",
        "cta_phone": "",
        "samples": [],
    }
    if post is None:
        return state

    for key in (
        "name", "category", "sub_type", "language", "team", "header_type",
        "header_text", "body", "footer", "button_kind",
        "cta_url_text", "cta_url", "cta_phone_text", "cta_phone",
    ):
        state[key] = post.get(key, state[key]).strip()

    if state["category"] not in CATEGORY_BY_KEY:
        state["category"] = DEFAULT_CATEGORY
    offered = {option.key for option in SUB_TYPES[state["category"]]}
    if state["sub_type"] not in offered:
        state["sub_type"] = SUB_TYPES[state["category"]][0].key
    if state["header_type"] not in HEADER_TYPE_KEYS:
        state["header_type"] = "none"
    if state["button_kind"] not in BUTTON_KIND_KEYS:
        state["button_kind"] = "none"

    state["quick_replies"] = [
        post.get(f"quick_reply_{i}", "").strip() for i in (1, 2, 3)
    ]
    state["samples"] = [
        {"number": number, "value": post.get(f"sample_{number}", "").strip()}
        for number in body_variables(state["body"])
    ]
    return state


def validate(state: dict, files) -> dict:
    """Server-side validation of a submitted editor state. Returns field ->
    Spanish error message; empty means the template can be saved. First
    failure per field wins, so each field shows one message."""
    errors = {}

    name = state["name"]
    if not name:
        errors["name"] = "Escribe un nombre para la plantilla."
    elif not NAME_RE.match(name):
        errors["name"] = "Solo letras minúsculas, números y guiones bajos (_)."
    elif len(name) > NAME_MAX:
        errors["name"] = f"Máximo {NAME_MAX} caracteres."
    elif MessageTemplate.objects.filter(
        name=name, language=state["language"]
    ).exists():
        errors["name"] = "Ya existe una plantilla con este nombre en este idioma."

    if state["language"] not in LANGUAGE_BY_CODE:
        errors["language"] = "Elige un idioma de la lista."

    if len(state["team"]) > TEAM_MAX:
        errors["team"] = f"Máximo {TEAM_MAX} caracteres."

    if state["header_type"] == "text":
        if not state["header_text"]:
            errors["header_text"] = "Escribe el texto de la cabecera."
        elif len(state["header_text"]) > HEADER_TEXT_MAX:
            errors["header_text"] = f"Máximo {HEADER_TEXT_MAX} caracteres."
        elif VARIABLE_RE.search(state["header_text"]):
            # There is no header-sample input, so no variables here.
            errors["header_text"] = "La cabecera no admite variables."
    elif state["header_type"] in MEDIA_HEADER_KEYS:
        upload = files.get("header_media")
        if not upload:
            errors["header_media"] = "Sube un archivo de ejemplo para la cabecera."
        elif not (getattr(upload, "content_type", "") or "").startswith(
            MEDIA_CONTENT_PREFIXES[state["header_type"]]
        ):
            errors["header_media"] = "El archivo no coincide con el tipo de cabecera elegido."
        elif upload.size > MEDIA_MAX_BYTES:
            errors["header_media"] = "El archivo supera el máximo de 16 MB."

    body = state["body"]
    if not body:
        errors["body"] = "El cuerpo del mensaje es obligatorio."
    elif len(body) > BODY_MAX:
        errors["body"] = f"Máximo {BODY_MAX} caracteres."
    elif PADDED_VARIABLE_RE.search(body):
        errors["body"] = "Escribe las variables sin ceros a la izquierda: {{1}}, {{2}}..."
    else:
        numbers = body_variables(body)
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            errors["body"] = "Numera las variables desde {{1}} y sin saltos."
        elif any(not sample["value"] for sample in state["samples"]):
            errors["samples"] = "Escribe un valor de ejemplo para cada variable."

    if len(state["footer"]) > FOOTER_MAX:
        errors["footer"] = f"Máximo {FOOTER_MAX} caracteres."
    elif VARIABLE_RE.search(state["footer"]):
        errors["footer"] = "El pie de página no admite variables."

    if state["button_kind"] == "quick":
        texts = [text for text in state["quick_replies"] if text]
        if not texts:
            errors["buttons"] = "Escribe al menos un botón de respuesta rápida."
        elif any(len(text) > BUTTON_TEXT_MAX for text in texts):
            errors["buttons"] = f"Cada botón admite máximo {BUTTON_TEXT_MAX} caracteres."
    elif state["button_kind"] == "cta":
        url_pair = (state["cta_url_text"], state["cta_url"])
        phone_pair = (state["cta_phone_text"], state["cta_phone"])
        if not any(url_pair) and not any(phone_pair):
            errors["buttons"] = "Completa al menos un botón de URL o de teléfono."
        elif any(url_pair) and not all(url_pair):
            errors["buttons"] = "El botón de URL necesita texto y URL."
        elif any(phone_pair) and not all(phone_pair):
            errors["buttons"] = "El botón de teléfono necesita texto y número."
        elif state["cta_url"] and not _valid_cta_url(state["cta_url"]):
            errors["buttons"] = (
                "Escribe una URL válida que empiece por http:// o https://."
            )
        elif state["cta_phone"] and not PHONE_RE.match(state["cta_phone"]):
            errors["buttons"] = "Escribe un número de teléfono válido."
        elif any(
            len(text) > BUTTON_TEXT_MAX
            for text in (state["cta_url_text"], state["cta_phone_text"])
        ):
            errors["buttons"] = f"Cada botón admite máximo {BUTTON_TEXT_MAX} caracteres."

    return errors


def _valid_cta_url(url: str) -> bool:
    """True for a real http(s) URL -- rejects "https://" with no host and
    other strings a bare startswith() check would let through."""
    try:
        _CTA_URL_VALIDATOR(url)
    except ValidationError:
        return False
    return True


def template_spec(template) -> TemplateSpec:
    """A MessageTemplate as the provider-neutral spec ``create_template``
    takes -- the one place the model crosses into ``messaging.providers``."""
    return TemplateSpec(
        name=template.name,
        language=template.language,
        category=template.category,
        body=template.body,
        body_sample_values=list(template.body_sample_values or []),
        header_type=template.header_type,
        header_text=template.header_text,
        header_media=template.header_media if template.header_media else None,
        footer=template.footer,
        buttons=list(template.buttons or []),
    )


def build_buttons(state: dict) -> list[dict]:
    """The buttons JSON stored on the model, from a validated state."""
    if state["button_kind"] == "quick":
        return [
            {"type": "quick_reply", "text": text}
            for text in state["quick_replies"]
            if text
        ]
    if state["button_kind"] == "cta":
        buttons = []
        if state["cta_url"]:
            buttons.append(
                {"type": "url", "text": state["cta_url_text"], "url": state["cta_url"]}
            )
        if state["cta_phone"]:
            buttons.append(
                {
                    "type": "phone",
                    "text": state["cta_phone_text"],
                    "phone": state["cta_phone"],
                }
            )
        return buttons
    return []


def model_kwargs(state: dict, files) -> dict:
    """The MessageTemplate.objects.create kwargs for a validated state.
    Fields belonging to unselected header/button choices save empty, so a
    switched-away choice leaves no orphan data behind."""
    header_type = state["header_type"]
    return {
        "name": state["name"],
        "category": state["category"],
        "sub_type": state["sub_type"],
        "language": state["language"],
        "team": state["team"],
        "header_type": header_type,
        "header_text": state["header_text"] if header_type == "text" else "",
        "header_media": (
            files.get("header_media") if header_type in MEDIA_HEADER_KEYS else ""
        ),
        "body": state["body"],
        # Numeric order regardless of where each {{n}} first appears, so
        # element i is always the sample for {{i+1}}.
        "body_sample_values": [
            sample["value"]
            for sample in sorted(state["samples"], key=lambda s: s["number"])
        ],
        "footer": state["footer"],
        "buttons": build_buttons(state),
        "status": "pendiente",
    }
