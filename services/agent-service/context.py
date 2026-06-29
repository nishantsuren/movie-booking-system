"""Booking context: the parameters actually required to book a ticket,
nothing else. Resolved values only (real ids/dates) -- nothing here is
ever written directly from free text or an LLM's own guess.
"""
from dataclasses import dataclass, field


@dataclass
class BookingContext:
    session_id: str
    user_id: str | None = None
    city_id: str | None = None
    movie_id: str | None = None
    date: str | None = None
    theatre_id: str | None = None
    showtime_id: str | None = None
    count: int | None = None
    seat_ids: list[str] = field(default_factory=list)
    booking_id: str | None = None
