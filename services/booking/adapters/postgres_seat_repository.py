"""SHOWTIME_SEAT queries used by the booking saga (§5.6). Plain psycopg2
on a per-request connection passed in by the caller -- this module never
calls commit()/rollback() itself, matching the rest of this codebase's
convention of leaving transaction boundaries to the API route handler.
"""
from typing import Optional

# §5.4 read-time reconciliation: a LOCKED seat past its lock_expires_at is
# treated as available *now*, regardless of whether the sweep worker
# (adapters/reconciliation_sweep.py) has physically flipped it back yet.
# Expressed once here and reused in both places it matters below --
# whether a seat reads as available, and whether a new lock attempt may
# claim it -- so the two can't drift out of sync with each other.
_EFFECTIVELY_AVAILABLE_SQL = "(status = 'AVAILABLE' OR (status = 'LOCKED' AND lock_expires_at < now()))"


class PostgresSeatRepository:
    def __init__(self, conn):
        self._conn = conn

    def get_available_for_booking(self, showtime_id: str, seat_ids: list[str]) -> dict[str, dict]:
        """Returns {seat_id: row} for the requested seats that currently
        exist for this showtime, regardless of status -- existence is
        checked here for a clean error. Each row carries
        `is_effectively_available` (§5.4) computed by the database
        itself, so the caller's availability check uses the same clock
        and the same rule that lock_seats's conditional UPDATE enforces,
        rather than re-deriving it in Python against a possibly-skewed
        local clock."""
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT *, {_EFFECTIVELY_AVAILABLE_SQL} AS is_effectively_available "
                "FROM showtime_seat WHERE showtime_id = %s AND id::text = ANY(%s)",
                (showtime_id, seat_ids),
            )
            rows = cur.fetchall()
        return {str(row["id"]): dict(row) for row in rows}

    def lock_seats(self, showtime_id: str, seat_ids: list[str], booking_id: str, lock_expires_at) -> int:
        """Postgres-side reflection of the Redis lock (§5.3 "Postgres as
        the actual backstop") -- AVAILABLE -> LOCKED, conditional on
        being effectively available (§5.4: AVAILABLE outright, or LOCKED
        with an already-expired hold -- claimable even before the sweep
        worker gets to it). Returns the number of seats actually flipped;
        the caller compares this against len(seat_ids) to detect a
        Redis/Postgres inconsistency."""
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE showtime_seat
                SET status = 'LOCKED', locked_by_booking_id = %(booking_id)s, lock_expires_at = %(lock_expires_at)s,
                    updated_at = now()
                WHERE showtime_id = %(showtime_id)s AND id::text = ANY(%(seat_ids)s) AND {_EFFECTIVELY_AVAILABLE_SQL}
                RETURNING id
                """,
                {
                    "booking_id": booking_id,
                    "lock_expires_at": lock_expires_at,
                    "showtime_id": showtime_id,
                    "seat_ids": seat_ids,
                },
            )
            return len(cur.fetchall())

    def mark_booked(self, showtime_id: str, booking_id: str) -> list[str]:
        """§5.3 point 1, §5.6 -- the actual correctness guarantee. Scoped
        to this booking's own locked seats (no seat_ids needed: derived
        from locked_by_booking_id), conditional on still being LOCKED.
        Returns the seat IDs actually flipped to BOOKED -- empty means
        either a racing confirm already won or the sweep worker (§5.4)
        already expired this hold (flipping these seats back to
        AVAILABLE), the caller (orchestrator) disambiguates.

        Deliberately no `lock_expires_at > now()` condition here (v14) --
        the sweep worker is the sole wall-clock authority for invalidating
        a hold; confirm only checks state (LOCKED), not time, so a confirm
        that reaches this UPDATE before the sweep's own pass always wins,
        even a moment past expires_at, rather than racing the same clock
        comparison the sweep already owns."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE showtime_seat
                SET status = 'BOOKED', updated_at = now()
                WHERE showtime_id = %(showtime_id)s AND locked_by_booking_id = %(booking_id)s
                  AND status = 'LOCKED'
                RETURNING id
                """,
                {"showtime_id": showtime_id, "booking_id": booking_id},
            )
            return [str(r["id"]) for r in cur.fetchall()]

    def release_to_available(self, showtime_id: str, booking_id: str) -> list[str]:
        """Cancellation path (§5.6): revert this booking's locked seats
        back to AVAILABLE. Returns the seat IDs reverted, so the caller
        knows which Redis lock keys to release."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE showtime_seat
                SET status = 'AVAILABLE', locked_by_booking_id = NULL, lock_expires_at = NULL, updated_at = now()
                WHERE showtime_id = %(showtime_id)s AND locked_by_booking_id = %(booking_id)s AND status = 'LOCKED'
                RETURNING id
                """,
                {"showtime_id": showtime_id, "booking_id": booking_id},
            )
            return [str(r["id"]) for r in cur.fetchall()]

    def get_locked_seat_ids(self, showtime_id: str, booking_id: str) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM showtime_seat WHERE showtime_id = %s AND locked_by_booking_id = %s",
                (showtime_id, booking_id),
            )
            return [str(r["id"]) for r in cur.fetchall()]
