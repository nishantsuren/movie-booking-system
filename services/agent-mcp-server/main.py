"""MCP server exposing the booking platform as tools for the hybrid
agent (CLAUDE.md / docs/design.md v24). Two kinds of tools, drawn as a
hard line:

- Five read-only lookup tools (list_cities, search_movies, get_movie,
  get_showtimes, get_seatmap) -- thin wrappers over platform_client.py,
  itself incapable of mutating anything (no select_seats/confirm_booking/
  cancel_booking/create_payment method exists anywhere in this service).
  An outer LLM calling these can answer a wrong question; it cannot take
  a wrong booking action.

- One stateful tool, handle_booking_turn -- the ONLY tool that can ever
  touch BookingContext, lock a seat, create a payment, or confirm a
  booking. It is a thin HTTP wrapper over agent-service's own internal
  POST /internal/handle-turn endpoint, which runs the existing, fully
  unchanged nlu.py + dialogue_manager.py pipeline. This server cannot
  run that pipeline itself -- it's a separate OS process from
  agent-service and cannot share its in-memory SessionStore.

Run via `uvicorn main:app --port 8008`, same launch convention as
every other FastAPI-based service in scripts/dev.sh's start_service --
FastMCP's streamable_http_app() exposes a plain ASGI app for exactly
this, so this service needs no separate launch pattern of its own.
"""
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from config import AGENT_SERVICE_TIMEOUT_SECONDS, AGENT_SERVICE_URL, PORT
from platform_client import NotFoundError, PlatformClient, PlatformUnavailableError

mcp = FastMCP("movie-booking-platform", host="0.0.0.0", port=PORT)
_platform = PlatformClient()


# Tool results below are trimmed to customer-relevant fields before
# being handed to the model -- empirically, a small local model
# summarizing a raw platform dict (idempotency_key, created_at/
# updated_at, is_active, poster_asset_id, ...) sometimes garbles or
# drops list items entirely (observed: a city's own `state` field
# being reported back as if it were a second city). Trimming at the
# source removes the ambiguity rather than hoping the model's own
# judgment about what's "internal" holds up turn after turn.
def _trim_city(city: dict) -> dict:
    return {"id": city["id"], "name": city["name"]}


def _trim_movie(movie: dict) -> dict:
    return {
        "id": movie["id"],
        "title": movie["title"],
        "description": movie.get("description"),
        "duration_minutes": movie.get("duration_minutes"),
        "language": movie.get("language"),
    }


def _trim_showtime(showtime: dict) -> dict:
    return {
        "id": showtime["id"],
        "start_time": showtime["start_time"],
        "theatre_name": showtime.get("theatre_name"),
        "screen_name": showtime.get("screen_name"),
        "base_price": showtime.get("base_price"),
    }

# Plain ASGI app for `uvicorn main:app` -- mcp.run() is for a process
# that owns the whole event loop itself; this codebase launches every
# service the same uvicorn-driven way (start_service in scripts/dev.sh),
# so this is the seam that keeps that convention from needing an
# exception just for this one component.
app = mcp.streamable_http_app()


@mcp.tool()
def list_cities() -> list[dict]:
    """List every city the movie booking platform currently supports.
    Use this to answer questions like "which cities do you support?"
    or "where can I book tickets?". Returns each city's id and name --
    the id is needed for search_movies/get_showtimes."""
    return [_trim_city(c) for c in _platform.list_cities()]


@mcp.tool()
def search_movies(city_id: str, query: Optional[str] = None) -> list[dict]:
    """List movies currently playing in a given city, optionally
    filtered by a title/description substring. Use this to answer
    "what's playing in <city>?" or "is <movie> showing here?". city_id
    can be either a city's id or just its plain name (e.g. "Bengaluru")
    -- both work, no need to call list_cities first just to resolve it."""
    try:
        return [_trim_movie(m) for m in _platform.search_movies(city_id, query)]
    except NotFoundError as exc:
        return [{"error": str(exc)}]
    except PlatformUnavailableError as exc:
        return [{"error": str(exc)}]


@mcp.tool()
def get_movie(movie_id: str) -> dict:
    """Get details (title, description, duration, language) for one
    specific movie by its id."""
    try:
        return _trim_movie(_platform.get_movie(movie_id))
    except NotFoundError as exc:
        return {"error": str(exc)}
    except PlatformUnavailableError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_showtimes(movie_id: str, city_id: str, date: str) -> dict:
    """List showtimes for a specific movie, in a specific city, on a
    specific ISO date (YYYY-MM-DD). Returns the movie's own details
    alongside the showtime list. city_id can be either a city's id or
    just its plain name."""
    try:
        result = _platform.get_showtimes(movie_id, city_id, date)
        return {
            "movie": _trim_movie(result["movie"]),
            "showtimes": [_trim_showtime(s) for s in result["showtimes"]],
        }
    except NotFoundError as exc:
        return {"error": str(exc)}
    except PlatformUnavailableError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_seatmap(showtime_id: str) -> dict:
    """Get the full seatmap (every seat, its label, type, price, and
    current status) for one specific showtime. Read-only -- does not
    hold or reserve anything."""
    try:
        return _platform.get_seatmap(showtime_id)
    except NotFoundError as exc:
        return {"error": str(exc)}
    except PlatformUnavailableError as exc:
        return {"error": str(exc)}


@mcp.tool()
def handle_booking_turn(session_id: str, message: str) -> dict:
    """Continue or start the customer's movie ticket booking flow --
    city/movie/date/showtime/seat selection, payment, and confirmation.
    Call this whenever the customer is trying to book tickets, check
    on an existing booking, or cancel one. Do not call any other tool
    in the same turn as this one, and relay this tool's own "response"
    field back to the customer exactly as written -- never paraphrase
    or add to it, since it may contain real booking/payment details."""
    try:
        resp = httpx.post(
            f"{AGENT_SERVICE_URL}/internal/handle-turn",
            json={"session_id": session_id, "message": message},
            timeout=AGENT_SERVICE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return {
            "_terminal": True,
            "response": "Sorry, the booking assistant is temporarily unavailable.",
            "state": "idle",
            "extra": {},
            "error": str(exc),
        }

    data["_terminal"] = True
    return data
