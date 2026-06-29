"""Thin httpx wrapper over the booking platform, called through the
routing service only (never a backend service directly).
"""
import httpx

from config import BOOKING_PLATFORM_URL, PLATFORM_CLIENT_TIMEOUT_SECONDS


class PlatformUnavailableError(RuntimeError):
    pass


def list_cities() -> list[dict]:
    """GET /theatre/cities via routing -- theatre owns CITY (no
    normalized data here yet, see design.md catalog's city_id note)."""
    try:
        resp = httpx.get(
            f"{BOOKING_PLATFORM_URL}/theatre/cities",
            timeout=PLATFORM_CLIENT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise PlatformUnavailableError(str(exc)) from exc


def list_movies(city_id: str | None = None) -> list[dict]:
    """GET /catalog/movies[?city=<city_id>] via routing -- active movies
    whose release window is currently running in that city (§4.4) when
    city_id is given. city_id is optional -- omitted, this returns
    every active movie across every city, the same "no filter" shape
    list_theatres() already has, used by resolution.py's name pool
    (which is deliberately not city-scoped)."""
    try:
        resp = httpx.get(
            f"{BOOKING_PLATFORM_URL}/catalog/movies",
            params={"city": city_id} if city_id else None,
            timeout=PLATFORM_CLIENT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise PlatformUnavailableError(str(exc)) from exc


def list_theatres(city_id: str | None = None) -> list[dict]:
    """GET /theatre/theatres[?city=<city_id>] via routing. city_id is
    optional -- omitted, this returns every theatre, which is how a
    theatre's home city gets derived when the user names a theatre
    before any city is set."""
    try:
        resp = httpx.get(
            f"{BOOKING_PLATFORM_URL}/theatre/theatres",
            params={"city": city_id} if city_id else None,
            timeout=PLATFORM_CLIENT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise PlatformUnavailableError(str(exc)) from exc


def list_showtimes_for_movie(movie_id: str, city_id: str) -> dict:
    """GET /theatre/movies/{movie_id}/showtimes?city=<city_id> via
    routing -- active showtimes plus the movie's own details in one
    response (theatre service already enriches it that way)."""
    try:
        resp = httpx.get(
            f"{BOOKING_PLATFORM_URL}/theatre/movies/{movie_id}/showtimes",
            params={"city": city_id},
            timeout=PLATFORM_CLIENT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise PlatformUnavailableError(str(exc)) from exc


def get_booking(booking_id: str) -> dict | None:
    """GET /booking/bookings/{booking_id} via routing. Returns None on
    404 -- an unknown/stale booking_id (e.g. a returning browser tab
    presenting one that's since vanished) is "nothing to report," not
    a hard failure -- distinct from PlatformUnavailableError, reserved
    for the platform being unreachable/erroring, a distinction no other
    function here needs since every other lookup uses a server-chosen
    id that's always real."""
    try:
        resp = httpx.get(
            f"{BOOKING_PLATFORM_URL}/booking/bookings/{booking_id}",
            timeout=PLATFORM_CLIENT_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise PlatformUnavailableError(str(exc)) from exc
