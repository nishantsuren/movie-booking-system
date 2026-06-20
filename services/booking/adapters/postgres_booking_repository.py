"""BOOKING queries (§5.6). Same per-request-connection, no-commit-here
convention as postgres_seat_repository.py.
"""
from typing import Optional

from domain.booking import Booking


class PostgresBookingRepository:
    def __init__(self, conn):
        self._conn = conn

    def get_live_by_idempotency_key(self, idempotency_key: str) -> Optional[Booking]:
        """Matches the partial unique index (§11.1 v12) -- only a still-
        live (PENDING/CONFIRMED) booking is a genuine idempotent replay;
        a terminal one must not block a fresh attempt."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM booking WHERE idempotency_key = %s AND status IN ('PENDING', 'CONFIRMED')",
                (idempotency_key,),
            )
            row = cur.fetchone()
        return Booking.from_row(row) if row else None

    def get(self, booking_id: str) -> Optional[Booking]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM booking WHERE id = %s", (booking_id,))
            row = cur.fetchone()
        return Booking.from_row(row) if row else None

    def create_pending(
        self,
        idempotency_key: str,
        user_id: str,
        showtime_id: str,
        movie_title: str,
        seat_labels: str,
        price_paid: float,
        expires_at,
    ) -> Optional[Booking]:
        """INSERT ... ON CONFLICT against the partial unique index --
        requires an explicit WHERE clause matching the index predicate
        (plain ON CONFLICT (idempotency_key) doesn't match a partial
        index), which is why this can't go through the shared
        IdempotentWriter helper. Returns None if a concurrent identical
        request won the race; the caller falls back to
        get_live_by_idempotency_key."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO booking (idempotency_key, user_id, showtime_id, movie_title, seat_labels, price_paid, expires_at)
                VALUES (%(idempotency_key)s, %(user_id)s, %(showtime_id)s, %(movie_title)s, %(seat_labels)s, %(price_paid)s, %(expires_at)s)
                ON CONFLICT (idempotency_key) WHERE status IN ('PENDING', 'CONFIRMED') DO NOTHING
                RETURNING *
                """,
                {
                    "idempotency_key": idempotency_key,
                    "user_id": user_id,
                    "showtime_id": showtime_id,
                    "movie_title": movie_title,
                    "seat_labels": seat_labels,
                    "price_paid": price_paid,
                    "expires_at": expires_at,
                },
            )
            row = cur.fetchone()
        return Booking.from_row(row) if row else None

    def mark_confirmed(self, booking_id: str) -> Optional[Booking]:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE booking SET status = 'CONFIRMED', updated_at = now() WHERE id = %s AND status = 'PENDING' RETURNING *",
                (booking_id,),
            )
            row = cur.fetchone()
        return Booking.from_row(row) if row else None

    def mark_cancelled(self, booking_id: str) -> Optional[Booking]:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE booking SET status = 'CANCELLED', updated_at = now() WHERE id = %s AND status = 'PENDING' RETURNING *",
                (booking_id,),
            )
            row = cur.fetchone()
        return Booking.from_row(row) if row else None
