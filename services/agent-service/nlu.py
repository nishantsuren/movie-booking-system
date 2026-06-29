"""NLU: the only place this service calls an LLM, exactly once per
turn, to pull raw text out of a free-form message. Never resolves
anything against the platform and never writes an id -- that's a
separate resolution step. Fails closed (all-None) on any Ollama
error or malformed output rather than raising, since a bad NLU call
must not crash the turn.
"""
import json

import httpx

from config import OLLAMA_MODEL, OLLAMA_TIMEOUT_SECONDS, OLLAMA_URL

# "showtime" is part of the canonical entities shape (dialogue_manager.py's
# CollectingShowtimeState reads entities.get("showtime")) but is never
# extracted here -- there's no LLM field or prompt example for it, only
# ever populated via a UI button click (see
# dialogue_manager.entities_from_selected_option), deliberately not via
# free-text parsing of an arbitrary time-of-day phrase. Listing it here
# only ensures _empty_result() always includes the key; extract()'s own
# parsing loop below never references it, so this is a zero-risk
# addition with no effect on the actual model prompt/call.
_FIELDS = ("city", "movie", "theatre", "date", "count", "showtime")

# Deliberately minimal -- llama3.2:3b is sensitive to prompt length for
# this task. An earlier version with bulleted field descriptions + 5
# examples regressed badly under direct testing against the live model:
# at temperature 0 (deterministic) it sometimes returned bare "{}" for
# messages an example covered almost verbatim, and adding back even one
# short instruction sentence made it echo the first example's values
# outright for an unrelated message instead of processing the real one.
# Two examples, no preamble, ending on a bare "JSON:" completion cue,
# is what actually held up across a city/movie/date/count test matrix.
#
# Second example was originally a date-only message ("what is playing
# tomorrow"). Swapped for a short theatre-only one after live testing of
# COLLECTING_THEATRE showed short follow-ups naming just a theatre
# ("how about PVR Orion Mall", "I meant PVR orion mall") were getting
# misclassified as city or movie instead -- neither prior example showed
# a short message dominated by a *theatre* name, so the model had
# nothing to pattern-match it against. Trades away the date-only
# example, which is an acceptable loss since date isn't consumed by any
# state yet (see agent_service_progress.md's known gaps).
_PROMPT_TEMPLATE = """Extract city, movie, theatre, date, count from the message as JSON.

Message: book 2 tickets for Pushpa 2 this Saturday at PVR Forum in Bengaluru
JSON: {{"city": "Bengaluru", "movie": "Pushpa 2", "theatre": "PVR Forum", "date": "Saturday", "count": 2}}

Message: how about PVR Orion Mall
JSON: {{"city": null, "movie": null, "theatre": "PVR Orion Mall", "date": null, "count": null}}

Message: {user_message}
JSON:"""


def _empty_result() -> dict:
    return {field: None for field in _FIELDS}


def _coerce_count(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract(message: str) -> dict:
    prompt = _PROMPT_TEMPLATE.format(user_message=message)

    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json()["response"])
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return _empty_result()

    if not isinstance(parsed, dict):
        return _empty_result()

    result = _empty_result()
    for field in ("city", "movie", "theatre", "date"):
        value = parsed.get(field)
        result[field] = value if isinstance(value, str) and value else None
    result["count"] = _coerce_count(parsed.get("count"))
    return result
