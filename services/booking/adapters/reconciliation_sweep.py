"""Reconciliation sweep worker (design §5.4) -- the sole mechanism that
flips an abandoned PENDING booking to EXPIRED and its SHOWTIME_SEAT rows
back to AVAILABLE. Single active instance across N replicas, elected via
a Postgres advisory lock held on a dedicated, non-pooled connection;
standbys poll for the lock and take over automatically if the active
instance's connection drops.

Deliberately a standalone process, not wired into the FastAPI app --
this has its own scaling/redundancy profile (N replicas, exactly one
active) independent of request-handling capacity (Appendix B: lives in
adapters/, not api/). Run directly: `python -m adapters.reconciliation_sweep`
from services/booking, with DATABASE_URL set.

v18/§5.7: also enqueues a RELEASE_HOLD Outbox entry (same table/relay
BookingOrchestrator.cancel() uses) for each expired booking that had an
external theatre hold -- in the same transaction as the expiry itself,
skipped cleanly for NULL theatre_hold_id (pre-v18 bookings, §13).
"""
import logging
import os
import time
from typing import Callable, Optional

import psycopg2

from config import RECONCILIATION_BATCH_SIZE, RECONCILIATION_LOCK_KEY, RECONCILIATION_LOCK_RETRY_SECONDS, RECONCILIATION_POLL_INTERVAL_SECONDS

logger = logging.getLogger("reconciliation_sweep")


class ReconciliationSweepWorker:
    def __init__(
        self,
        database_url: str,
        poll_interval_seconds: float = RECONCILIATION_POLL_INTERVAL_SECONDS,
        lock_retry_seconds: float = RECONCILIATION_LOCK_RETRY_SECONDS,
        batch_size: int = RECONCILIATION_BATCH_SIZE,
    ):
        self._database_url = database_url
        self._poll_interval_seconds = poll_interval_seconds
        self._lock_retry_seconds = lock_retry_seconds
        self._batch_size = batch_size
        self._lock_conn: Optional["psycopg2.extensions.connection"] = None
        self._last_sweep_conn: Optional["psycopg2.extensions.connection"] = None
        self._running = False

        # Test-only instrumentation, both None in production:
        # - _after_select_hook: called right after candidates are SELECTed,
        #   before the booking UPDATE -- lets a test commit a concurrent
        #   confirm() in that exact window to race it against this sweep
        #   pass's own (PENDING-guarded) UPDATE.
        # - _after_booking_update_hook: called right after the booking
        #   UPDATE (uncommitted), before the showtime_seat UPDATE -- lets a
        #   test pause mid-transaction and force-close the connection to
        #   simulate this instance dying before commit, then confirm no
        #   half-processed booking results.
        self._after_select_hook: Optional[Callable[[], None]] = None
        self._after_booking_update_hook: Optional[Callable[[], None]] = None

    @property
    def is_active(self) -> bool:
        return self._lock_conn is not None and not self._lock_conn.closed

    def try_become_active(self) -> bool:
        """Attempt to acquire the advisory lock on a fresh, dedicated
        connection. Idempotent: if we already hold it, returns True
        without doing anything. The lock is session-scoped -- it is
        automatically released if this connection ever closes or dies,
        which is exactly the failover trigger (§5.4)."""
        if self.is_active:
            return True

        conn = psycopg2.connect(self._database_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (RECONCILIATION_LOCK_KEY,))
                acquired = cur.fetchone()[0]
        except Exception:
            conn.close()
            raise

        if acquired:
            self._lock_conn = conn
            logger.info("acquired reconciliation advisory lock -- now active")
            return True
        conn.close()
        return False

    def release_active(self) -> None:
        if self._lock_conn is None:
            return
        try:
            if not self._lock_conn.closed:
                with self._lock_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (RECONCILIATION_LOCK_KEY,))
        except Exception:
            pass
        finally:
            try:
                self._lock_conn.close()
            except Exception:
                pass
            self._lock_conn = None

    def run_one_sweep_pass(self) -> int:
        """One bounded batch: SELECT candidates, then both UPDATEs in a
        single transaction on a separate (pooled-style, per-call)
        connection from the lock connection (§5.4 point 2). Returns the
        number of bookings actually expired this pass.

        The booking UPDATE re-checks status = 'PENDING' even though the
        candidates were just selected as PENDING moments ago -- a
        concurrent confirm() could have committed in between, and without
        this guard a sweep pass could clobber a booking that just won a
        legitimate race (§5.6). This is what test 3 exercises directly.
        """
        conn = psycopg2.connect(self._database_url)
        # Test-only instrumentation: exposes the in-flight connection so a
        # test can force-close it mid-transaction to simulate this
        # instance crashing (a real connection drop, not a fake signal).
        # Unused in production.
        self._last_sweep_conn = conn
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM booking WHERE status = 'PENDING' AND expires_at < now() "
                    "ORDER BY expires_at LIMIT %s",
                    (self._batch_size,),
                )
                # id::text = ANY(%s) (not bare id = ANY(%s)): psycopg2 adapts
                # a plain Python list to a text[] array, and Postgres has no
                # uuid = text operator -- this is the same cast convention
                # used throughout postgres_seat_repository.py.
                candidate_ids = [str(row[0]) for row in cur.fetchall()]

            if self._after_select_hook is not None:
                self._after_select_hook()

            if not candidate_ids:
                conn.commit()
                return 0

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE booking SET status = 'EXPIRED', updated_at = now() "
                    "WHERE id::text = ANY(%s) AND status = 'PENDING' RETURNING id, theatre_hold_id",
                    (candidate_ids,),
                )
                expired_rows = [(str(row[0]), row[1]) for row in cur.fetchall()]
                expired_ids = [row[0] for row in expired_rows]

            if self._after_booking_update_hook is not None:
                self._after_booking_update_hook()

            if expired_ids:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE showtime_seat SET status = 'AVAILABLE', locked_by_booking_id = NULL, "
                        "lock_expires_at = NULL, updated_at = now() "
                        "WHERE locked_by_booking_id::text = ANY(%s) AND status = 'LOCKED'",
                        (expired_ids,),
                    )

                # §5.7/§13: release_hold for each expired booking that had
                # an external hold, via the same Outbox the confirm/cancel
                # paths use -- a separate relay retries it independently,
                # in the *same transaction* as the expiry itself, same as
                # BookingOrchestrator.cancel(). Skipped for NULL
                # theatre_hold_id (pre-v18 bookings, §13).
                with conn.cursor() as cur:
                    for booking_id, theatre_hold_id in expired_rows:
                        if theatre_hold_id is not None:
                            cur.execute(
                                "INSERT INTO pending_theatre_call (call_type, booking_id, theatre_hold_id) "
                                "VALUES ('RELEASE_HOLD', %s, %s)",
                                (booking_id, theatre_hold_id),
                            )

            conn.commit()
            if expired_ids:
                logger.info("reconciliation sweep expired %d booking(s)", len(expired_ids))
            return len(expired_ids)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass  # connection may already be dead (e.g. force-closed) -- don't mask the original error
            raise
        finally:
            conn.close()

    def stop(self) -> None:
        self._running = False
        self.release_active()

    def run_forever(self) -> None:
        self._running = True
        while self._running:
            if self.try_become_active():
                try:
                    self.run_one_sweep_pass()
                except Exception:
                    logger.exception("sweep pass failed")
                time.sleep(self._poll_interval_seconds)
            else:
                time.sleep(self._lock_retry_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    worker = ReconciliationSweepWorker(database_url=os.environ["DATABASE_URL"])
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
