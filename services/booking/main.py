"""Booking service — Phase 3-5.

SHOWTIME_SEAT + internal materialize endpoint (§4.3, §5.3, Phase 3);
RedisSeatLocker (§5.1/§5.2, Phase 4); BOOKING + the full saga -- select
seats, mocked payment, confirm, cancel (§5.6, §7/§8, Phase 5).
"""
import hashlib
import os
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from adapters.payment_client import PaymentClient, PaymentNotFound, PaymentServiceUnavailable
from adapters.postgres_booking_repository import PostgresBookingRepository
from adapters.postgres_seat_repository import PostgresSeatRepository
from adapters.redis_seat_locker import RedisSeatLocker
from adapters.showtime_meta_repository import ShowtimeMetaRepository
from application.booking_orchestrator import BookingOrchestrator
from db import get_db
from domain.booking import (
    BookingHoldExpired,
    BookingNotFound,
    ConfirmConflict,
    InvalidBookingState,
    PaymentNotValid,
    SeatsUnavailable,
    ShowtimeNotMaterialized,
)
from shared.auth.auth import AuthContext, get_auth_context
from shared.events.events import LoggingEventPublisher

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

app = FastAPI(title="Booking service")

_event_publisher = LoggingEventPublisher()
_payment_client = PaymentClient()
_seat_locker = RedisSeatLocker()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "booking", "auth_enabled": AUTH_ENABLED}


def _derive_idempotency_key(*parts: object) -> str:
    """Same convention as catalog/theatre's _derive_idempotency_key
    (§11.1). Duplicated rather than shared, consistent with how each
    service already keeps its own copy."""
    normalized = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _derive_booking_idempotency_key(showtime_id: UUID, user_id: UUID, seat_ids: list[UUID]) -> str:
    """§11.1 v12: showtime_id + user_id + seat_ids canonicalized by
    sorting then comma-joining, so request order never matters and the
    same key is re-derived on a genuine retry."""
    canonical_seats = ",".join(sorted(str(s) for s in seat_ids))
    return _derive_idempotency_key(showtime_id, user_id, canonical_seats)


def _build_orchestrator(conn) -> BookingOrchestrator:
    return BookingOrchestrator(
        seats=PostgresSeatRepository(conn),
        locker=_seat_locker,
        bookings=PostgresBookingRepository(conn),
        showtime_meta=ShowtimeMetaRepository(conn),
        payments=_payment_client,
        events=_event_publisher,
    )


def _booking_to_dict(booking) -> dict:
    return {
        "id": booking.id,
        "idempotency_key": booking.idempotency_key,
        "user_id": booking.user_id,
        "showtime_id": booking.showtime_id,
        "movie_title": booking.movie_title,
        "seat_labels": booking.seat_labels,
        "price_paid": booking.price_paid,
        "status": booking.status.value,
        "expires_at": booking.expires_at.isoformat(),
    }


# --- internal materialize endpoint (Appendix C, called by theatre service) ---

class MaterializeSeat(BaseModel):
    seat_template_id: UUID
    label: str
    x: float
    y: float
    seat_type: str
    price: float


class MaterializeRequest(BaseModel):
    movie_title: str
    seats: list[MaterializeSeat]
    # Phase 8: theatre/screen/time/price context for the seatmap page,
    # cached here rather than fetched live (see showtime_meta_repository.py).
    # Optional with defaults so a payload from before this phase (e.g. a
    # direct test call) still validates.
    theatre_name: str = ""
    screen_name: str = ""
    start_time: Optional[datetime] = None
    base_price: Optional[float] = None


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

    # §4.3/§11.1 v12, extended Phase 8: cache showtime display context
    # locally so neither booking creation nor the seatmap read ever needs
    # a live cross-service call on the booking hot path.
    ShowtimeMetaRepository(conn).upsert(
        str(showtime_id),
        body.movie_title,
        theatre_name=body.theatre_name,
        screen_name=body.screen_name,
        start_time=body.start_time,
        base_price=body.base_price,
    )
    conn.commit()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM showtime_seat WHERE showtime_id = %s ORDER BY label",
            (str(showtime_id),),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"showtime_id": str(showtime_id), "seats": rows}


@app.get("/showtimes/{showtime_id}/seatmap")
def get_seatmap(showtime_id: UUID, conn=Depends(get_db)) -> dict:
    """Appendix A, enriched (Phase 8, design v16): wraps the seat array
    with the showtime-level display context the seatmap page needs
    (movie/theatre/screen/time/price), all served from the local
    showtime_meta cache -- no live cross-service call on this, the
    system's highest-volume read path (§2)."""
    meta = ShowtimeMetaRepository(conn).get(str(showtime_id))
    if meta is None:
        raise HTTPException(status_code=404, detail="showtime not found or not materialized")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, label, position_x AS x, position_y AS y, seat_type, price, status "
            "FROM showtime_seat WHERE showtime_id = %s ORDER BY label",
            (str(showtime_id),),
        )
        seats = [dict(r) for r in cur.fetchall()]

    return {
        "showtime_id": str(showtime_id),
        "movie_title": meta["movie_title"],
        "theatre_name": meta["theatre_name"],
        "screen_name": meta["screen_name"],
        "start_time": meta["start_time"].isoformat() if meta["start_time"] else None,
        "base_price": meta["base_price"],
        "seats": seats,
    }


# --- booking saga (Appendix A, §5.6, §7/§8) ---

class BookingCreate(BaseModel):
    showtime_id: UUID
    seat_ids: list[UUID]
    user_id: UUID


class ConfirmRequest(BaseModel):
    payment_id: UUID


@app.post("/bookings", status_code=201)
def create_booking(
    body: BookingCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    if not body.seat_ids:
        raise HTTPException(status_code=400, detail="seat_ids must not be empty")

    idempotency_key = _derive_booking_idempotency_key(body.showtime_id, body.user_id, body.seat_ids)
    orchestrator = _build_orchestrator(conn)
    try:
        booking = orchestrator.select_seats(
            str(body.showtime_id), [str(s) for s in body.seat_ids], str(body.user_id), idempotency_key
        )
    except ShowtimeNotMaterialized:
        conn.rollback()
        raise HTTPException(status_code=404, detail="showtime not found or not materialized")
    except SeatsUnavailable as exc:
        conn.rollback()
        raise HTTPException(
            status_code=409,
            detail={"detail": "seats unavailable", "conflicting_seat_ids": exc.conflicting_seat_ids},
        )

    conn.commit()
    return _booking_to_dict(booking)


@app.get("/bookings/{booking_id}")
def get_booking(
    booking_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    booking = PostgresBookingRepository(conn).get(str(booking_id))
    if booking is None:
        raise HTTPException(status_code=404, detail="booking not found")
    return _booking_to_dict(booking)


@app.post("/bookings/{booking_id}/confirm")
def confirm_booking(
    booking_id: UUID,
    body: ConfirmRequest,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    orchestrator = _build_orchestrator(conn)
    try:
        booking = orchestrator.confirm(str(booking_id), str(body.payment_id))
    except BookingNotFound:
        conn.rollback()
        raise HTTPException(status_code=404, detail="booking not found")
    except BookingHoldExpired:
        conn.rollback()
        raise HTTPException(status_code=409, detail="booking hold has expired")
    except InvalidBookingState as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ConfirmConflict:
        conn.rollback()
        raise HTTPException(status_code=409, detail="seats were not in the expected locked state")
    except PaymentNotFound:
        conn.rollback()
        raise HTTPException(status_code=404, detail="payment not found")
    except PaymentNotValid as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except PaymentServiceUnavailable as exc:
        conn.rollback()
        raise HTTPException(status_code=503, detail=f"payment service unavailable: {exc}")

    conn.commit()
    return _booking_to_dict(booking)


@app.delete("/bookings/{booking_id}")
def cancel_booking(
    booking_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    orchestrator = _build_orchestrator(conn)
    try:
        booking = orchestrator.cancel(str(booking_id))
    except BookingNotFound:
        conn.rollback()
        raise HTTPException(status_code=404, detail="booking not found")
    except InvalidBookingState as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc))

    conn.commit()
    return _booking_to_dict(booking)
