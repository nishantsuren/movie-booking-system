"""Admin-only showtime CRUD + seat materialization (§4.3, Appendix C).
A SHOWTIME is created inactive (is_active=false) -- materialization
still happens unconditionally at creation, so a showtime can never be
activated without a complete seat inventory already in place. There is
no hard delete: DELETE just flips is_active back to false (design v10 --
the original "delete only if no active bookings" contract didn't fit a
single point-in-time screening with no duration of its own).
"""
import random
import time
from datetime import datetime
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException

from admin.lock import list_seats
from admin.schemas import ShowtimeCreate, ShowtimeUpdate
from common.config import BOOKING_SERVICE_URL, MATERIALIZE_BASE_DELAY_SECONDS, MATERIALIZE_MAX_ATTEMPTS
from common.db import get_db
from common.idempotency import derive_idempotency_key
from shared.auth.auth import AuthContext, require_role

router = APIRouter(prefix="/admin")


class MaterializationFailed(RuntimeError):
    """Raised when the booking service's materialize-seats call doesn't
    succeed within the bounded retry budget, or rejects the payload
    outright -- the caller must roll back showtime creation on this (§4.3
    fail-closed: no orphan showtime with zero bookable seats)."""


def _materialize_seats_with_retry(
    showtime_id: UUID,
    movie_title: str,
    seats_payload: list[dict],
    theatre_name: str = "",
    screen_name: str = "",
    start_time: Optional[datetime] = None,
    base_price: Optional[float] = None,
) -> dict:
    url = f"{BOOKING_SERVICE_URL}/internal/showtimes/{showtime_id}/materialize-seats"
    payload = {
        "movie_title": movie_title,
        "seats": seats_payload,
        "theatre_name": theatre_name,
        "screen_name": screen_name,
        "start_time": start_time.isoformat() if start_time else None,
        "base_price": base_price,
    }
    last_error: Optional[str] = None
    for attempt in range(MATERIALIZE_MAX_ATTEMPTS):
        try:
            resp = httpx.post(url, json=payload, timeout=5.0)
        except httpx.TransportError as exc:
            last_error = f"transport error calling booking service: {exc}"
        else:
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code < 500:
                # Client error (bad payload) -- not transient, retrying won't help.
                raise MaterializationFailed(
                    f"booking service rejected materialize payload: {resp.status_code} {resp.text}"
                )
            last_error = f"booking service returned {resp.status_code}: {resp.text}"

        if attempt < MATERIALIZE_MAX_ATTEMPTS - 1:
            delay = MATERIALIZE_BASE_DELAY_SECONDS * (2**attempt)
            time.sleep(delay + random.uniform(0, delay * 0.5))

    raise MaterializationFailed(
        f"exhausted {MATERIALIZE_MAX_ATTEMPTS} attempts calling booking service: {last_error}"
    )


def _get_showtime_or_404(conn, showtime_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM showtime WHERE id = %s", (str(showtime_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="showtime not found")
    return dict(row)


@router.get("/screens/{screen_id}/showtimes")
def list_showtimes_for_screen(
    screen_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> list[dict]:
    """Phase 9: admin showtime management (edit/activate/deactivate
    existing ones) needs to list them first -- unlike the customer-facing
    GET /movies/{id}/showtimes, this returns every showtime regardless of
    is_active, since deactivated ones are exactly what an admin managing
    them needs to see too."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM screen WHERE id = %s", (str(screen_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="screen not found")
        cur.execute("SELECT * FROM showtime WHERE screen_id = %s ORDER BY start_time", (str(screen_id),))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/showtimes", status_code=201)
def create_showtime(
    body: ShowtimeCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.name AS screen_name, t.name AS theatre_name "
            "FROM screen s JOIN theatre t ON t.id = s.theatre_id WHERE s.id = %s",
            (str(body.screen_id),),
        )
        screen_row = cur.fetchone()
        if screen_row is None:
            raise HTTPException(status_code=404, detail="screen not found")

    with conn.cursor() as cur:
        # Assumes at most one ACTIVE layout per screen (design's stated
        # SCREEN-SEAT_LAYOUT cardinality) -- a pre-existing Phase 2 gap means
        # nothing actually enforces that today (publishing a new draft never
        # deactivates a screen's prior ACTIVE layout). Out of this phase's
        # scope to fix; noted here since fetchone() would otherwise silently
        # pick an arbitrary one if it were ever violated.
        cur.execute(
            "SELECT id FROM seat_layout WHERE screen_id = %s AND status = 'ACTIVE'",
            (str(body.screen_id),),
        )
        layout = cur.fetchone()
    if layout is None:
        raise HTTPException(status_code=409, detail="screen has no published (ACTIVE) seat layout")

    seats = [s for s in list_seats(conn, layout["id"]) if s["is_active"]]
    if not seats:
        raise HTTPException(status_code=409, detail="published seat layout has no active seats")

    idempotency_key = derive_idempotency_key(body.screen_id, body.start_time)

    # Deliberately not using IdempotentWriter here: it commits the INSERT
    # immediately, but fail-closed (§4.3) requires the showtime row to stay
    # uncommitted until materialization actually succeeds -- one
    # transaction, all or nothing.
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO showtime (idempotency_key, movie_id, movie_title, screen_id, start_time, is_high_demand, base_price)
            VALUES (%(idempotency_key)s, %(movie_id)s, %(movie_title)s, %(screen_id)s, %(start_time)s, %(is_high_demand)s, %(base_price)s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            {
                "idempotency_key": idempotency_key,
                "movie_id": str(body.movie_id),
                "movie_title": body.movie_title,
                "screen_id": str(body.screen_id),
                "start_time": body.start_time,
                "is_high_demand": body.is_high_demand,
                "base_price": body.base_price,
            },
        )
        row = cur.fetchone()

    if row is None:
        # Already created (and, by construction, already successfully
        # materialized) by an earlier identical request -- clean no-op.
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM showtime WHERE idempotency_key = %s", (idempotency_key,))
            existing = cur.fetchone()
        return dict(existing)

    showtime = dict(row)
    seats_payload = [
        {
            "seat_template_id": s["id"],
            "label": s["label"],
            "x": s["position_x"],
            "y": s["position_y"],
            "seat_type": s["seat_type"],
            "price": body.base_price * s["price_multiplier"],
        }
        for s in seats
    ]

    try:
        _materialize_seats_with_retry(
            showtime["id"],
            body.movie_title,
            seats_payload,
            theatre_name=screen_row["theatre_name"],
            screen_name=screen_row["screen_name"],
            start_time=body.start_time,
            base_price=body.base_price,
        )
    except MaterializationFailed as exc:
        conn.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"seat materialization failed, showtime not created: {exc}",
        )

    conn.commit()
    return showtime


@router.put("/showtimes/{showtime_id}")
def update_showtime(
    showtime_id: UUID,
    body: ShowtimeUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    """Updates theatre service's own SHOWTIME row only. Does not
    re-materialize or touch already-materialized SHOWTIME_SEAT rows in
    booking service -- e.g. changing base_price here does not retroactively
    reprice seats already materialized at creation time. Out of scope for
    this phase; screen_id is intentionally not updatable here at all."""
    _get_showtime_or_404(conn, showtime_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return _get_showtime_or_404(conn, showtime_id)
    if "movie_id" in fields and fields["movie_id"] is not None:
        fields["movie_id"] = str(fields["movie_id"])

    set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
    fields["showtime_id"] = str(showtime_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE showtime SET {set_clause}, updated_at = now() WHERE id = %(showtime_id)s RETURNING *",
            fields,
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row)


@router.post("/showtimes/{showtime_id}/activate")
def activate_showtime(
    showtime_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE showtime SET is_active = true, updated_at = now() WHERE id = %s RETURNING *",
            (str(showtime_id),),
        )
        row = cur.fetchone()
    if row is None:
        conn.rollback()
        raise HTTPException(status_code=404, detail="showtime not found")
    conn.commit()
    return dict(row)


@router.delete("/showtimes/{showtime_id}")
def deactivate_showtime(
    showtime_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    """Flips is_active back to false. No row removal (design v10)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE showtime SET is_active = false, updated_at = now() WHERE id = %s RETURNING *",
            (str(showtime_id),),
        )
        row = cur.fetchone()
    if row is None:
        conn.rollback()
        raise HTTPException(status_code=404, detail="showtime not found")
    conn.commit()
    return dict(row)
