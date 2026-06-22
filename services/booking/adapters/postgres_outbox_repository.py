"""PENDING_THEATRE_CALL queries (design §5.7/§13, v18) -- the Outbox for
confirm_hold/release_hold retries. `enqueue` shares the caller's
per-request connection (same no-commit-here convention as
postgres_seat_repository.py/postgres_booking_repository.py) so the
enqueue is part of the same transaction as the booking confirm/cancel
that triggered it -- if that transaction rolls back, the enqueued call
never happened either. The relay (theatre_outbox_relay.py) uses its own
connection and is the only thing that ever reads these rows back.
"""
from typing import Optional


class PostgresOutboxRepository:
    def __init__(self, conn):
        self._conn = conn

    def enqueue(self, call_type: str, booking_id: str, theatre_hold_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pending_theatre_call (call_type, booking_id, theatre_hold_id) VALUES (%s, %s, %s)",
                (call_type, booking_id, theatre_hold_id),
            )

    def fetch_due(self, batch_size: int) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM pending_theatre_call WHERE status = 'PENDING' AND next_attempt_at <= now() "
                "ORDER BY next_attempt_at LIMIT %s",
                (batch_size,),
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_done(self, call_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE pending_theatre_call SET status = 'DONE', updated_at = now() WHERE id = %s",
                (call_id,),
            )

    def record_failure(self, call_id: str, error: str, next_attempt_at, give_up: bool) -> None:
        """`give_up` (bounded attempts exhausted, §13: "flag for manual
        reconciliation -- an ops concern, not a data-integrity one")
        moves the row to FAILED instead of scheduling yet another retry."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_theatre_call
                SET attempts = attempts + 1,
                    last_error = %(error)s,
                    status = CASE WHEN %(give_up)s THEN 'FAILED' ELSE 'PENDING' END,
                    next_attempt_at = %(next_attempt_at)s,
                    updated_at = now()
                WHERE id = %(id)s
                """,
                {"id": call_id, "error": error, "next_attempt_at": next_attempt_at, "give_up": give_up},
            )

    def get(self, call_id: str) -> Optional[dict]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM pending_theatre_call WHERE id = %s", (call_id,))
            row = cur.fetchone()
        return dict(row) if row else None

    def list_for_booking(self, booking_id: str) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM pending_theatre_call WHERE booking_id = %s ORDER BY created_at",
                (booking_id,),
            )
            return [dict(r) for r in cur.fetchall()]
