"""TheatreIntegration Protocol (design §5.7/§8, v18) -- the external lock
against the theatre's own ticketing system. Plain dataclasses + a
Protocol only, no framework imports, same convention as domain/booking.py
and the SeatLocker Protocol it's modeled after.

The Redis lock (§5.1) protects BookMyShow's own concurrent users; this
protects against every other channel (theatre's own site, other
aggregators, box office) booking the same seat at the same time. Both
are required -- neither replaces the other (§5.7).
"""
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class HoldResult:
    success: bool
    theatre_hold_id: Optional[str] = None    # populated on success -- opaque token from theatre's system
    conflicting_seat_ids: list[str] = field(default_factory=list)  # populated on conflict


@dataclass
class SeatStatus:
    seat_id: str
    status: str  # mirrors SHOWTIME_SEAT.status's vocabulary: AVAILABLE / LOCKED / BOOKED


class TheatreIntegrationUnavailable(RuntimeError):
    """The theatre API call itself failed (timeout/5xx) or the circuit
    breaker is open -- distinct from a conflict (HoldResult.success =
    False), which is a normal, expected outcome, not a failure. Maps to
    503 at the API layer (§13: "Theatre API times out or returns 5xx
    during hold_seats... Return 503 (retryable)")."""


class TheatreIntegration(Protocol):
    def hold_seats(self, showtime_id: str, seat_ids: list[str], hold_duration_seconds: int) -> HoldResult: ...
    def confirm_hold(self, theatre_hold_id: str) -> None: ...
    def release_hold(self, theatre_hold_id: str) -> None: ...
    def sync_availability(self, showtime_id: str) -> list[SeatStatus]: ...
