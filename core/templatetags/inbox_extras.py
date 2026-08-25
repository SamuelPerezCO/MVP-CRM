"""Small template helpers for the Inbox screen."""

from django import template

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
