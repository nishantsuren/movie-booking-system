"""Booking service — Phase 3.

Only SHOWTIME_SEAT and the internal materialize endpoint (§4.3, §5.3)
land here. No locking, no BOOKING table, no payment -- those are
Phase 4/5 and deliberately not pulled forward just because this service
now exists.
"""
import os
from uuid import UUID

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from db import get_db

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

app = FastAPI(title="Booking service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "booking", "auth_enabled": AUTH_ENABLED}


# --- internal materialize endpoint (Appendix C, called by theatre service) ---

class MaterializeSeat(BaseModel):
    seat_template_id: UUID
    label: str
    x: float
    y: float
    seat_type: str
    price: float


class MaterializeRequest(BaseModel):
    seats: list[MaterializeSeat]


@app.post("/internal/showtimes/{showtime_id}/materialize-seats", status_code=201)
def materialize_seats(
    showtime_id: UUID,
    body: MaterializeRequest,
    conn=Depends(get_db),
) -> dict:
    """Idempotent via the UNIQUE (showtime_id, seat_template_id) constraint
    itself (§5.3 point 2) -- a retried call for the same showtime is a clean
    per-row no-op through ON CONFLICT DO NOTHING, not a separately derived
    hash key. The full current row set is always returned, regardless of
    how many rows this particular call actually inserted."""
    with conn.cursor() as cur:
        for seat in body.seats:
            cur.execute(
                """
                INSERT INTO showtime_seat
                    (showtime_id, seat_template_id, label, position_x, position_y, seat_type, price)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (showtime_id, seat_template_id) DO NOTHING
                """,
                (
                    str(showtime_id),
                    str(seat.seat_template_id),
                    seat.label,
                    seat.x,
                    seat.y,
                    seat.seat_type,
                    seat.price,
                ),
            )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM showtime_seat WHERE showtime_id = %s ORDER BY label",
            (str(showtime_id),),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"showtime_id": str(showtime_id), "seats": rows}
