"""Read-only wrapper over the routing service -- deliberately a subset
of services/agent-service/booking_client.py's methods, duplicated
rather than imported cross-service (this codebase's existing
convention: small per-service client logic is copied, not shared --
see _derive_idempotency_key across catalog/theatre).

The duplication here is also a deliberate safety boundary, not just
convention-following: this MCP server's only HTTP capability against
the platform is read-only browse/lookup. There is no select_seats,
confirm_booking, cancel_booking, or create_payment method anywhere in
this file -- those stay reachable only through the handle_booking_turn
tool's call into agent-service's own internal endpoint (main.py),
which is the sole path to BookingContext/seat locks/payments. A model
calling a tool from this file structurally cannot mutate booking
state, not merely instructed not to.
"""
import re
from typing import Optional

import httpx

from config import BOOKING_PLATFORM_URL, PLATFORM_CLIENT_TIMEOUT_SECONDS

# requirements doc's own alias map (dialogue_manager.py's CITY_ALIASES) --
# duplicated here for the same reason every small per-service client is
# duplicated rather than imported across these two services.
_CITY_ALIASES = {"blore": "Bengaluru", "bombay": "Mumbai", "madras": "Chennai"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


class PlatformUnavailableError(RuntimeError):
    pass


class NotFoundError(RuntimeError):
    pass


class PlatformClient:
    def __init__(self, base_url: str = BOOKING_PLATFORM_URL):
        self._client = httpx.Client(base_url=base_url, timeout=PLATFORM_CLIENT_TIMEOUT_SECONDS)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise PlatformUnavailableError(str(exc)) from exc

    def _raise_for_platform_error(self, resp: httpx.Response) -> None:
        if resp.status_code == 503 or resp.status_code >= 500:
            raise PlatformUnavailableError(f"platform returned {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    def _resolve_city_id(self, city: str) -> str:
        """search_movies/get_showtimes both take a city_id, but a small
        local model calling these tools frequently passes the city NAME
        it was just given instead of chaining a list_cities() call
        first to get the id, even when both tools' own descriptions say
        to -- empirically observed, not hypothetical. Rather than rely
        on prompt compliance from an unreliable small model, the tool
        itself resolves a name to an id the same way
        dialogue_manager.py's _resolve_city does (same alias map), so
        the chain works regardless of which form the model passes."""
        if _UUID_RE.match(city):
            return city
        cities = self.list_cities()
        needle = _CITY_ALIASES.get(city.strip().lower(), city.strip().lower())
        matches = [c for c in cities if needle in c["name"].lower() or c["name"].lower() in needle]
        if not matches:
            raise NotFoundError(f"city '{city}' not recognised")
        exact = [c for c in matches if c["name"].lower() == needle]
        return (exact[0] if exact else matches[0])["id"]

    def list_cities(self) -> list[dict]:
        resp = self._request("GET", "/theatre/cities")
        self._raise_for_platform_error(resp)
        return resp.json()

    def search_movies(self, city_id: str, query: Optional[str] = None) -> list[dict]:
        """Same client-side substring filter as booking_client.py's
        version -- catalog has no server-side title search, and no
        genre column at all (see that file's docstring for the same
        gap, applies identically here)."""
        city_id = self._resolve_city_id(city_id)
        resp = self._request("GET", "/catalog/movies", params={"city": city_id})
        self._raise_for_platform_error(resp)
        movies = resp.json()
        if not query:
            return movies
        needle = query.strip().lower()
        return [
            m
            for m in movies
            if needle in m["title"].lower() or needle in (m.get("description") or "").lower()
        ]

    def get_movie(self, movie_id: str) -> dict:
        resp = self._request("GET", f"/catalog/movies/{movie_id}")
        if resp.status_code == 404:
            raise NotFoundError(f"movie {movie_id} not found")
        self._raise_for_platform_error(resp)
        return resp.json()

    def get_showtimes(self, movie_id: str, city_id: str, date: str) -> dict:
        city_id = self._resolve_city_id(city_id)
        resp = self._request(
            "GET", f"/theatre/movies/{movie_id}/showtimes", params={"city": city_id, "date": date}
        )
        if resp.status_code == 404:
            raise NotFoundError(f"movie {movie_id} not found")
        self._raise_for_platform_error(resp)
        return resp.json()

    def get_seatmap(self, showtime_id: str) -> dict:
        resp = self._request("GET", f"/booking/showtimes/{showtime_id}/seatmap")
        if resp.status_code == 404:
            raise NotFoundError(f"showtime {showtime_id} not found or not materialized")
        self._raise_for_platform_error(resp)
        return resp.json()
