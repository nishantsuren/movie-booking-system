"""Theatre availability sync job (design §5.7's "sync-based shadow
inventory") -- periodically pulls each active showtime's seat
availability from the theatre's system via `theatre.sync_availability()`
and reconciles any drift into SHOWTIME_SEAT, so the seatmap read path
(§2's highest-volume read) keeps serving the local shadow copy and never
makes a live call to the theatre's system itself.

Same structural pattern as reconciliation_sweep.py and
theatre_outbox_relay.py: a standalone process, single active instance
via a Postgres advisory lock (distinct key), N replicas for redundancy.
Run directly: `python -m adapters.theatre_availability_sync` from
services/booking, with DATABASE_URL set.

Only an AVAILABLE seat reported by the theatre as taken needs
reconciling (§5.7: "a seat taken on another portal will appear available
in BookMyShow until the next sync" -- the failure mode this job exists
to bound the window of). A seat this system already has LOCKED/BOOKED is
left alone: it is either mid-booking under this system's own lock (the
hold_seats call already reflects this seat as taken on the theatre's
side too) or already booked, and a `sync_availability` that reported it
back as available would be theatre-side staleness, not something to
overwrite local state with -- the external hold/booking, not the
periodic sync, is what's authoritative for seats this system already
holds.
"""
import logging
import os
import time
from typing import Optional

import psycopg2
import psycopg2.extras

from adapters.mock_theatre_integration import MockTheatreIntegration
from config import (
    AVAILABILITY_SYNC_LOCK_KEY,
    AVAILABILITY_SYNC_LOCK_RETRY_SECONDS,
    AVAILABILITY_SYNC_POLL_INTERVAL_SECONDS,
    AVAILABILITY_SYNC_SHOWTIME_BATCH_SIZE,
)
from domain.theatre_integration import TheatreIntegration

logger = logging.getLogger("theatre_availability_sync")


class TheatreAvailabilitySyncWorker:
    def __init__(
        self,
        database_url: str,
        theatre: Optional[TheatreIntegration] = None,
        poll_interval_seconds: float = AVAILABILITY_SYNC_POLL_INTERVAL_SECONDS,
        lock_retry_seconds: float = AVAILABILITY_SYNC_LOCK_RETRY_SECONDS,
        showtime_batch_size: int = AVAILABILITY_SYNC_SHOWTIME_BATCH_SIZE,
    ):
        self._database_url = database_url
        self._theatre = theatre or MockTheatreIntegration(database_url=database_url)
        self._poll_interval_seconds = poll_interval_seconds
        self._lock_retry_seconds = lock_retry_seconds
        self._showtime_batch_size = showtime_batch_size
        self._lock_conn: Optional["psycopg2.extensions.connection"] = None
        self._running = False

    @property
    def is_active(self) -> bool:
        return self._lock_conn is not None and not self._lock_conn.closed

    def try_become_active(self) -> bool:
        """Same election mechanics as ReconciliationSweepWorker -- see
        reconciliation_sweep.py's docstring for the full reasoning."""
        if self.is_active:
            return True

        conn = psycopg2.connect(self._database_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (AVAILABILITY_SYNC_LOCK_KEY,))
                acquired = cur.fetchone()[0]
        except Exception:
            conn.close()
            raise

        if acquired:
            self._lock_conn = conn
            logger.info("acquired availability sync advisory lock -- now active")
            return True
        conn.close()
        return False

    def release_active(self) -> None:
        if self._lock_conn is None:
            return
        try:
            if not self._lock_conn.closed:
                with self._lock_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (AVAILABILITY_SYNC_LOCK_KEY,))
        except Exception:
            pass
        finally:
            try:
                self._lock_conn.close()
            except Exception:
                pass
            self._lock_conn = None

    def _active_showtime_ids(self, conn) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT showtime_id FROM showtime_seat LIMIT %s",
                (self._showtime_batch_size,),
            )
            return [str(row["showtime_id"]) for row in cur.fetchall()]

    def run_one_sync_pass(self) -> int:
        """One bounded batch: for each showtime with materialized seats,
        pull theatre_availability and reconcile any AVAILABLE seat the
        theatre now reports as taken. Each showtime's reconciliation is
        its own transaction, so one showtime's sync failure never blocks
        the rest of the batch. Returns the number of seats actually
        reconciled this pass."""
        conn = psycopg2.connect(self._database_url, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            showtime_ids = self._active_showtime_ids(conn)
        finally:
            conn.close()

        reconciled_count = 0
        for showtime_id in showtime_ids:
            try:
                statuses = self._theatre.sync_availability(showtime_id)
            except Exception:
                logger.exception("availability sync failed for showtime %s", showtime_id)
                continue

            taken_seat_ids = [s.seat_id for s in statuses if s.status != "AVAILABLE"]
            if not taken_seat_ids:
                continue

            conn = psycopg2.connect(self._database_url)
            try:
                with conn.cursor() as cur:
                    # Only reconcile seats this system *still* believes are
                    # AVAILABLE -- a LOCKED/BOOKED seat is already accounted
                    # for under this system's own external hold/booking
                    # (see module docstring), not theatre-side staleness.
                    cur.execute(
                        "UPDATE showtime_seat SET status = 'BOOKED', updated_at = now() "
                        "WHERE showtime_id = %s AND id::text = ANY(%s) AND status = 'AVAILABLE' "
                        "RETURNING id",
                        (showtime_id, taken_seat_ids),
                    )
                    updated = cur.fetchall()
                conn.commit()
                reconciled_count += len(updated)
            finally:
                conn.close()

        if reconciled_count:
            logger.info("availability sync reconciled %d seat(s)", reconciled_count)
        return reconciled_count

    def stop(self) -> None:
        self._running = False
        self.release_active()

    def run_forever(self) -> None:
        self._running = True
        while self._running:
            if self.try_become_active():
                try:
                    self.run_one_sync_pass()
                except Exception:
                    logger.exception("availability sync pass failed")
                time.sleep(self._poll_interval_seconds)
            else:
                time.sleep(self._lock_retry_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    worker = TheatreAvailabilitySyncWorker(database_url=os.environ["DATABASE_URL"])
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
