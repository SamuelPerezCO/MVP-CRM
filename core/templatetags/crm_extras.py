"""Template helpers for the CRM screen."""

from django import template
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

register = template.Library()


@register.simple_tag
def flag_template(country: str) -> str:
    """Path to a vendored SVG flag for ``country``, or "" if none is vendored.

    Emoji flags are not an option on their own: Windows ships no flag glyphs, so
    a regional-indicator pair renders as bare letters ("CO") there. The template
    prefers a real SVG and falls back to the emoji only when one isn't vendored.

    To support another country, drop `templates/icons/flags/<cc>.svg` in place --
    this picks it up with no code change.
    """
    code = (country or "").lower()
    if len(code) != 2 or not code.isalpha():
        return ""

    path = f"icons/flags/{code}.svg"
    try:
        get_template(path)
    except TemplateDoesNotExist:
        return ""
    return path
