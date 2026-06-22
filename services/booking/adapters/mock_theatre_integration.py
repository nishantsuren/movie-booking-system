"""MockTheatreIntegration (design §5.7) -- always succeeds by default,
the same Dependency Inversion pattern as RedisSeatLocker/PaymentClient: a
mock for local dev/tests now, a real adapter per theatre POS system
later (§16.8), with zero BookingOrchestrator changes either way.

The three failure knobs below default to "always succeed" -- every
pre-existing test, and normal local operation, never touch them. They
exist purely so the Phase 9.5 failure-path tests can make this mock
behave like a real, occasionally-failing external system: every other
test in this codebase hits the real running service over HTTP rather
than a unit-tested fake (CLAUDE.md's testing convention), and there is
no real theatre API here to actually fail on demand -- this is what
makes that possible. `hold_mode` is read from an env var in main.py so
the *live* HTTP-wired process can be restarted into a failure mode for
tests (a)/(b); `confirm_hold_should_fail`/`release_hold_should_fail` are
set directly by tests that construct this class themselves (same
technique test_phase6.py uses for ReconciliationSweepWorker) to drive
the Outbox relay's retry path for tests (c)/(d), independent of
hold_seats' own behavior.
"""
import os
import uuid
from typing import Optional

import psycopg2
import psycopg2.extras

from adapters.circuit_breaker import CircuitBreaker
from domain.theatre_integration import HoldResult, SeatStatus, TheatreIntegrationUnavailable

DEFAULT_BREAKER_FAILURE_THRESHOLD = 3
DEFAULT_BREAKER_RECOVERY_SECONDS = 30.0

_VALID_HOLD_MODES = {"success", "conflict", "timeout"}


class MockTheatreIntegration:
    def __init__(
        self,
        hold_mode: str = "success",
        confirm_hold_should_fail: bool = False,
        release_hold_should_fail: bool = False,
        database_url: Optional[str] = None,
        breaker: Optional[CircuitBreaker] = None,
    ):
        if hold_mode not in _VALID_HOLD_MODES:
            raise ValueError(f"unknown hold_mode: {hold_mode!r}")
        self._hold_mode = hold_mode
        self._confirm_hold_should_fail = confirm_hold_should_fail
        self._release_hold_should_fail = release_hold_should_fail
        self._database_url = database_url or os.environ.get("DATABASE_URL")
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=DEFAULT_BREAKER_FAILURE_THRESHOLD,
            recovery_timeout_seconds=DEFAULT_BREAKER_RECOVERY_SECONDS,
            trips_on=TheatreIntegrationUnavailable,
        )

    def hold_seats(self, showtime_id: str, seat_ids: list[str], hold_duration_seconds: int) -> HoldResult:
        def _do() -> HoldResult:
            if self._hold_mode == "timeout":
                raise TheatreIntegrationUnavailable("theatre API timeout (simulated)")
            if self._hold_mode == "conflict":
                return HoldResult(success=False, conflicting_seat_ids=list(seat_ids))
            return HoldResult(success=True, theatre_hold_id=str(uuid.uuid4()))

        return self._breaker.call(_do)

    def confirm_hold(self, theatre_hold_id: str) -> None:
        if self._confirm_hold_should_fail:
            raise TheatreIntegrationUnavailable("confirm_hold failed (simulated)")
        # No-op otherwise -- there is no real theatre system locally to tell.

    def release_hold(self, theatre_hold_id: str) -> None:
        if self._release_hold_should_fail:
            raise TheatreIntegrationUnavailable("release_hold failed (simulated)")
        # No-op otherwise, same reasoning as confirm_hold.

    def sync_availability(self, showtime_id: str) -> list[SeatStatus]:
        """No real external system exists locally to have drifted from --
        mirrors this system's own SHOWTIME_SEAT status right back, which
        is by construction always "in sync" with itself. This still
        exercises the sync job's real read/diff/update plumbing in local
        dev; only a real adapter could ever report actual drift."""
        conn = psycopg2.connect(self._database_url, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, status FROM showtime_seat WHERE showtime_id = %s", (showtime_id,))
                rows = cur.fetchall()
        finally:
            conn.close()
        return [SeatStatus(seat_id=str(r["id"]), status=r["status"]) for r in rows]
