"""Theatre Outbox relay worker (design §5.7/§13, v18) -- retries
confirm_hold/release_hold calls written to the Outbox
(`pending_theatre_call`) by the booking confirm/cancel paths and the
reconciliation sweep's expiry path (§5.4), independently of those hot
paths. Same structural pattern as reconciliation_sweep.py: a standalone
process, not wired into the FastAPI app, single active instance via a
Postgres advisory lock (distinct key), N replicas for redundancy. Run
directly: `python -m adapters.theatre_outbox_relay` from services/booking,
with DATABASE_URL set.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from adapters.mock_theatre_integration import MockTheatreIntegration
from adapters.postgres_outbox_repository import PostgresOutboxRepository
from domain.theatre_integration import TheatreIntegration, TheatreIntegrationUnavailable

logger = logging.getLogger("theatre_outbox_relay")

# Distinct from reconciliation_sweep.py's RECONCILIATION_LOCK_KEY
# (84236501) -- must not collide with any other advisory lock key used
# against this database.
OUTBOX_RELAY_LOCK_KEY = 84236502

DEFAULT_POLL_INTERVAL_SECONDS = 10
DEFAULT_LOCK_RETRY_SECONDS = 5
DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE_SECONDS = 2.0  # next_attempt_at = now() + base * 2**attempts


class TheatreOutboxRelayWorker:
    def __init__(
        self,
        database_url: str,
        theatre: Optional[TheatreIntegration] = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        lock_retry_seconds: float = DEFAULT_LOCK_RETRY_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
    ):
        self._database_url = database_url
        self._theatre = theatre or MockTheatreIntegration(database_url=database_url)
        self._poll_interval_seconds = poll_interval_seconds
        self._lock_retry_seconds = lock_retry_seconds
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._backoff_base_seconds = backoff_base_seconds
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
                cur.execute("SELECT pg_try_advisory_lock(%s)", (OUTBOX_RELAY_LOCK_KEY,))
                acquired = cur.fetchone()[0]
        except Exception:
            conn.close()
            raise

        if acquired:
            self._lock_conn = conn
            logger.info("acquired outbox relay advisory lock -- now active")
            return True
        conn.close()
        return False

    def release_active(self) -> None:
        if self._lock_conn is None:
            return
        try:
            if not self._lock_conn.closed:
                with self._lock_conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (OUTBOX_RELAY_LOCK_KEY,))
        except Exception:
            pass
        finally:
            try:
                self._lock_conn.close()
            except Exception:
                pass
            self._lock_conn = None

    def run_one_relay_pass(self) -> int:
        """One bounded batch: fetch due rows (their own short
        transaction), then call the theatre API for each on its own
        connection/transaction -- one failing call never blocks the
        rest of the batch, and a crash mid-batch leaves only the
        in-flight row's attempt counter unbumped, not a half-applied
        update. Failure reschedules with exponential backoff; once
        max_attempts is exhausted the row moves to FAILED rather than
        retrying forever (§13: "flag for manual reconciliation -- an
        ops concern, not a data-integrity one"). Returns the number of
        calls successfully completed this pass."""
        fetch_conn = psycopg2.connect(self._database_url, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            due = PostgresOutboxRepository(fetch_conn).fetch_due(self._batch_size)
            fetch_conn.commit()
        finally:
            fetch_conn.close()

        done_count = 0
        for row in due:
            conn = psycopg2.connect(self._database_url, cursor_factory=psycopg2.extras.RealDictCursor)
            try:
                repo = PostgresOutboxRepository(conn)
                try:
                    if row["call_type"] == "CONFIRM_HOLD":
                        self._theatre.confirm_hold(row["theatre_hold_id"])
                    else:
                        self._theatre.release_hold(row["theatre_hold_id"])
                except TheatreIntegrationUnavailable as exc:
                    attempts_after = row["attempts"] + 1
                    give_up = attempts_after >= self._max_attempts
                    backoff_seconds = self._backoff_base_seconds * (2**row["attempts"])
                    repo.record_failure(
                        str(row["id"]),
                        str(exc),
                        next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds),
                        give_up=give_up,
                    )
                    conn.commit()
                    if give_up:
                        logger.error(
                            "pending_theatre_call %s exhausted retries -- flagged FAILED for manual reconciliation",
                            row["id"],
                        )
                    continue

                repo.mark_done(str(row["id"]))
                conn.commit()
                done_count += 1
            finally:
                conn.close()

        if due:
            logger.info("outbox relay processed %d due call(s), %d succeeded", len(due), done_count)
        return done_count

    def stop(self) -> None:
        self._running = False
        self.release_active()

    def run_forever(self) -> None:
        self._running = True
        while self._running:
            if self.try_become_active():
                try:
                    self.run_one_relay_pass()
                except Exception:
                    logger.exception("outbox relay pass failed")
                time.sleep(self._poll_interval_seconds)
            else:
                time.sleep(self._lock_retry_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    worker = TheatreOutboxRelayWorker(database_url=os.environ["DATABASE_URL"])
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        worker.stop()
