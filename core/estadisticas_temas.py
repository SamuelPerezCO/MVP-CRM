"""
Data layer for the Estadísticas > Temas de conversación panel: the period
filter and the word-frequency report behind the tiles and the ranking table.

Same placement rationale as ``core.estadisticas_volumen``: ``core.estadisticas``
is the section's nav definition, shared by every stat page; each built-out
page keeps its report in a sibling module.

What "tema" means here, honestly: the reference product detects conversation
topics with AI. This MVP has no AI pipeline, so a tema is a *word customers
actually use* -- inbound message bodies are tokenized, greetings/stopwords
are dropped, accents are folded so «envío» and «envio» count as one topic,
and what remains is ranked by how many conversations mention it. That is a
real count over real messages, and the page says so; when an AI classifier
lands, this module is the one thing it replaces.

Unlike volumen/tiempos this aggregation cannot happen in SQL -- it reads the
text -- so it is bounded two ways instead: only the most recent
:data:`SCAN_CAP` inbound messages are ever scanned, and the finished report
is cached per period (:data:`CACHE_SECONDS`), mirroring volumen's stance.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from messaging.models import Message

#: Hard cap on how many messages one report reads. Text analysis pulls rows
#: into Python, which "stops working long before anyone notices" (volumen's
#: words) -- so the scan is bounded to the newest messages of the period.
SCAN_CAP = 5000

#: How long one period's report is reused. Same value and reasoning as
#: volumen: fresh within minutes, but flipping periods back and forth never
#: re-reads five thousand bodies.
CACHE_SECONDS = 300

#: How many temas the table shows. Below the top ranks the counts collapse
#: into a long tail of ones, which is noise, not insight.
TOP_N = 15

#: Tokens shorter than this never count ("ok", "eh", "ya"...).
MIN_WORD_LENGTH = 3


@dataclass(frozen=True)
class Period:
    """One option in the period select."""

    key: str
    """Value in the URL / select option."""

    label: str
    """Visible Spanish label."""

    days: int | None
    """Window counting back from now; ``None`` means all history."""


#: Order here is the order rendered in the select.
PERIODS = [
    Period("7", "Últimos 7 días", 7),
    Period("30", "Últimos 30 días", 30),
    Period("90", "Últimos 90 días", 90),
    Period("todo", "Todo el historial", None),
]

PERIOD_BY_KEY = {period.key: period for period in PERIODS}

DEFAULT_PERIOD = "30"


def parse_period(data) -> Period:
    """The ``?period=`` value out of a GET QueryDict, falling back to the
    default rather than erroring -- a stale bookmark still opens."""
    return PERIOD_BY_KEY.get(data.get("period", ""), PERIOD_BY_KEY[DEFAULT_PERIOD])


# --- Tokenization -----------------------------------------------------------

#: Acute accents (and diéresis) folded for counting, so «envío» and «envio»
#: are one tema. Deliberately NOT full unicode-decomposition stripping: that
#: would also fold ñ and merge «año» into something else entirely.
_FOLD = str.maketrans("áéíóúü", "aeiouu")

#: Words that can never be a tema, in accent-folded form: articles,
#: prepositions, pronouns, the auxiliary/courtesy verbs every chat is made
#: of, and the greetings that open one. Curated for Spanish chat -- this is
#: a noise filter, not linguistics, and growing it is always safe. Words
#: shorter than MIN_WORD_LENGTH never reach the check, so «el», «de», «ya»
#: and friends need no entry. The ñ forms appear as typed («señor»,
#: «mañana»...) because the fold deliberately leaves ñ alone; the ñ-less
#: misspellings are listed too, since customers type both.
STOPWORDS = frozenset("""
    los las una unos unas del esta este esto estos estas esa ese eso
    esos esas aquel aquella aquello con como cuando donde entre hacia hasta
    para por segun sin sobre tras desde durante mediante ante bajo contra
    pero sino aunque porque pues que quien cual cuales cuanto cuanta
    cuantos cuantas mas menos muy tambien tampoco solo bien mal aqui ahi
    alli alla ahora antes despues luego hoy ayer siempre nunca
    manana mañana entonces asi casi todavia aun ademas igual osea sea
    usted ustedes ella ellos ellas nosotros vosotros les nos mis tus sus
    nuestro nuestra
    nuestros nuestras algo alguien nada nadie alguna alguno algunas algunos
    ninguna ninguno todo toda todos todas otra otro otras otros cada mismo
    misma
    ser soy eres somos son era eran fue fui sera estar estoy estas estamos
    estan
    estaba estaban hay haber has hemos han habia tener tengo tienes tiene
    tenemos tienen tenia hacer hago haces hace hacen hizo hice poder puedo
    puedes puede podemos pueden podria podrias querer quiero quieres quiere
    quieren quisiera necesito necesita decir digo dices dice dicen dijo
    saber sabes sabe ver veo ves dar doy das dan ira voy vas vamos van
    seria estaria
    hola buenos buenas dias tardes noches gracias favor porfa porfavor
    saludos senor señor senora señora don dona doña senorita señorita
    disculpa disculpe perdon
    listo dale vale okay claro perfecto genial excelente super bueno buena
    jaja jajaja jajajaja jeje sisi
    uno dos tres cuatro cinco seis siete ocho nueve diez cero
""".split())

_WORD = re.compile(r"[^\W\d_]+")


def tokenize(body: str):
    """Yield ``(key, raw)`` pairs: the accent-folded counting key and the
    word as the customer typed it (lowercased), stopwords and short tokens
    already dropped."""
    for raw in _WORD.findall(body.lower()):
        if len(raw) < MIN_WORD_LENGTH:
            continue
        key = raw.translate(_FOLD)
        if key in STOPWORDS:
            continue
        yield key, raw


# --- The report -------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    """One row of the ranking table."""

    word: str
    """Display form: the raw spelling customers used most often."""

    conversations: int
    """Distinct conversations mentioning it -- the ranking metric."""

    mentions: int
    """Total occurrences across all messages."""


def report(period: Period) -> dict:
    """The whole panel's data for one period, cached per period key.

    Only inbound messages count -- the page asks what *customers* talk
    about, and counting the agents' replies would answer with the agents'
    vocabulary instead.
    """
    cache_key = f"estadisticas_temas:{period.key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    messages = Message.objects.filter(direction=Message.INBOUND).exclude(body="")
    if period.days is not None:
        messages = messages.filter(
            timestamp__gte=timezone.now() - timedelta(days=period.days)
        )

    mentions: Counter[str] = Counter()
    conversations: dict[str, set[int]] = defaultdict(set)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    analyzed = 0
    seen_conversations: set[int] = set()

    rows = messages.order_by("-timestamp").values_list("conversation_id", "body")
    for conversation_id, body in rows[:SCAN_CAP].iterator():
        analyzed += 1
        seen_conversations.add(conversation_id)
        for key, raw in tokenize(body):
            mentions[key] += 1
            conversations[key].add(conversation_id)
            spellings[key][raw] += 1

    topics = sorted(
        (
            Topic(
                # The most common raw spelling; ties break alphabetically so
                # the same data always renders the same word.
                word=min(
                    spellings[key], key=lambda raw: (-spellings[key][raw], raw)
                ),
                conversations=len(conversations[key]),
                mentions=count,
            )
            for key, count in mentions.items()
        ),
        key=lambda topic: (-topic.conversations, -topic.mentions, topic.word),
    )

    built = {
        "topics": topics[:TOP_N],
        # What every row's bar is scaled against.
        "max_conversations": topics[0].conversations if topics else 0,
        "total_topics": len(topics),
        "analyzed_messages": analyzed,
        "conversation_count": len(seen_conversations),
    }
    cache.set(cache_key, built, CACHE_SECONDS)
    return built
