"""Deterministic resolution layer: runs on nlu.py's raw extraction,
before any DialogueState sees it. nlu.py's LLM call stays responsible
for *segmenting* a free-form message into candidate field values; it
is not reliable at categorizing a bare proper noun it has already
isolated (see agent_service_progress.md's "COLLECTING_THEATRE state"
notes -- a ceiling on what a 3B model can do at this, not a prompt-
wording bug). This module re-categorizes each candidate string against
the platform's real combined city+movie+theatre name pool using stdlib
difflib similarity, and normalizes the winning match to the platform's
exact name. No new dependency (no embeddings/cosine similarity) --
difflib.SequenceMatcher gets an equivalent score for short proper nouns
with zero new ones (decided in the prior session, see
agent_service_progress.md).
"""
import difflib

from config import RESOLUTION_MATCH_THRESHOLD
from platform_client import PlatformUnavailableError, list_cities, list_movies, list_theatres

_FIELDS = ("city", "movie", "theatre")


def _build_pool() -> list[tuple[str, str]]:
    """Every real city/movie/theatre name, unfiltered by each other --
    this runs before any slot is resolved, so there's no city/movie
    context yet to scope by. Returns [] (not raises) on platform
    unavailability so resolve() can fail open."""
    try:
        cities = list_cities()
        movies = list_movies()
        theatres = list_theatres()
    except PlatformUnavailableError:
        return []

    pool = [("city", city["name"]) for city in cities]
    pool += [("movie", movie["title"]) for movie in movies]
    pool += [("theatre", theatre["name"]) for theatre in theatres]
    return pool


def _similarity(needle: str, name: str) -> float:
    """needle and name are both already lowercased. A short fragment
    fully contained in a longer real name (e.g. "INOX" inside "INOX
    Mantri Square" -- nlu.py splitting one theatre name across two
    fields, the other half landing intact in a different field this
    same turn) is a strong signal on its own; plain ratio() penalizes
    it for the sheer length difference, the wrong shape for "shorter
    is truly a substring of longer" vs. "noisy near-miss". Containment
    short-circuits straight past that; length >=3 avoids 1-2 char
    needles trivially "containment-matching" almost everything."""
    if len(needle) >= 3 and needle in name:
        return 1.0
    return difflib.SequenceMatcher(None, needle, name).ratio()


def _best_match(raw: str, pool: list[tuple[str, str]]) -> tuple[str, str, float]:
    """The single best-scoring (category, name) pair in the whole pool
    for one raw candidate string -- deliberately not restricted to the
    category nlu.py originally guessed, since that guess is exactly
    what this layer exists to correct."""
    needle = raw.strip().lower()
    best_category, best_name, best_score = pool[0][0], pool[0][1], -1.0
    for category, name in pool:
        score = _similarity(needle, name.lower())
        if score > best_score:
            best_category, best_name, best_score = category, name, score
    return best_category, best_name, best_score


def resolve(entities: dict) -> dict:
    """Re-categorizes and normalizes entities["city"/"movie"/"theatre"]
    against the platform's real name pool. entities["date"]/["count"]
    pass through untouched -- nothing here consumes them. Best-effort:
    if the platform is unreachable, returns entities unchanged rather
    than blocking the turn, same fail-open posture as every other
    platform call in this service degrading gracefully."""
    pool = _build_pool()
    if not pool:
        return entities

    result = dict(entities)
    raw_by_field = {field: entities[field] for field in _FIELDS if entities.get(field)}
    if not raw_by_field:
        return result

    # Highest-scoring candidate claims its category first: the common
    # case this layer exists for is a single short message naming one
    # real-world entity under the wrong field, and processing in score
    # order means that one real match always wins its slot even when a
    # second, weaker candidate would otherwise compete for it.
    scored = sorted(
        ((*_best_match(raw, pool), field) for field, raw in raw_by_field.items()),
        key=lambda item: item[2],
        reverse=True,
    )

    claimed_categories: set[str] = set()
    for category, name, score, field in scored:
        if score < RESOLUTION_MATCH_THRESHOLD:
            # No credible match anywhere in the pool for this raw text
            # -- leave it exactly as nlu.py extracted it.
            continue
        if category not in claimed_categories:
            result[category] = name
            claimed_categories.add(category)
            if category != field:
                # Won a match under a *different* slot than nlu.py put
                # it in (the actual bug this layer fixes) -- clear the
                # original, now-wrong slot.
                result[field] = None
        elif field != category:
            # A different field's raw text already claimed this same
            # category with an equal-or-higher score. This is the
            # "INOX Mantri Square" case: nlu.py can split one real
            # entity across multiple fields (city="Mantri Square",
            # movie="INOX", theatre="INOX Mantri Square" from a single
            # theatre-only message) -- every one of those fragments
            # independently matches the same theatre, so the loser is
            # cleared too rather than left behind as a stale,
            # unrelated-looking value in its original slot.
            result[field] = None

    return result
