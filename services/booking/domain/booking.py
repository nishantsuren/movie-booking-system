"""Booking domain types (design §7/§8). Plain data + exceptions only --
state transitions are conditional SQL UPDATEs in the Postgres adapters
(§5.6), not in-process behavior, consistent with how every other service
in this codebase treats its database as the actual state machine.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class BookingStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass
class Booking:
    id: str
    idempotency_key: str
    user_id: str
    showtime_id: str
    movie_title: str
    seat_labels: str
    price_paid: float
    status: BookingStatus
    expires_at: datetime
    theatre_hold_id: Optional[str] = None  # v18/§5.7 -- NULL for pre-existing bookings (no backfill)

    @classmethod
    def from_row(cls, row: dict) -> "Booking":
        return cls(
            id=str(row["id"]),
            idempotency_key=row["idempotency_key"],
            user_id=str(row["user_id"]),
            showtime_id=str(row["showtime_id"]),
            movie_title=row["movie_title"],
            seat_labels=row["seat_labels"],
            price_paid=row["price_paid"],
            status=BookingStatus(row["status"]),
            expires_at=row["expires_at"],
            theatre_hold_id=row.get("theatre_hold_id"),
        )


class ShowtimeNotMaterialized(LookupError):
    """No SHOWTIME_META / SHOWTIME_SEAT rows for this showtime_id -- it was
    never materialized (§4.3), so there is nothing to book against."""


class SeatsUnavailable(RuntimeError):
    def __init__(self, conflicting_seat_ids: list[str]):
        self.conflicting_seat_ids = conflicting_seat_ids
        super().__init__(f"seats unavailable: {conflicting_seat_ids}")


class BookingNotFound(LookupError):
    pass


class InvalidBookingState(RuntimeError):
    """Booking exists but isn't in a state the requested operation allows
    (e.g. confirming an already-CANCELLED booking)."""


class BookingHoldExpired(RuntimeError):
    """The sweep worker (§5.4) has already flipped this booking to
    EXPIRED -- confirm no longer self-polices wall-clock expiry (v14),
    so this only fires once something has actually acted on it."""


class ConfirmConflict(RuntimeError):
    """The seat-level conditional update (§5.3) affected zero rows, and the
    booking is neither CONFIRMED (a legitimate race loss) nor expired --
    a genuine inconsistency that should never happen in normal operation."""


class PaymentNotValid(RuntimeError):
    """Payment exists but doesn't apply to this booking (wrong booking_id)
    or didn't succeed."""
