"""Cache-busting URLs for our own CSS/JS.

`{% static %}` alone emits a stable path like /static/css/plantillas.css, so a
browser (and Vercel's CDN) happily keeps serving the copy it cached before a
fix shipped -- a CSS change can land, deploy, and still not reach anyone who
already loaded the page. Vercel's Django build runs plain `collectstatic`
without hashed filenames, so nothing upstream solves this for us.

`{% vstatic %}` appends a short content hash as a query string, so the URL
changes exactly when the file's bytes do and the old cache entry stops being
used. Hashes are computed once per process outside DEBUG; in development they
are recomputed per render so an edit shows up on the next reload.
"""

import hashlib

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()

_hashes: dict[str, str] = {}


@register.simple_tag
def vstatic(path: str) -> str:
    """The `{% static %}` URL for ``path`` plus a ``?v=<hash>`` stamp."""
    url = static(path)
    digest = _content_hash(path)
    return f"{url}?v={digest}" if digest else url


def _content_hash(path: str) -> str:
    if not settings.DEBUG and path in _hashes:
        return _hashes[path]

    absolute = finders.find(path)
    digest = ""
    if absolute:
        with open(absolute, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()[:10]

    # Cache misses too ("" for a path with no file behind it): the answer can't
    # change without a redeploy, and retrying the lookup every render wouldn't
    # make the file appear.
    if not settings.DEBUG:
        _hashes[path] = digest
    return digest
