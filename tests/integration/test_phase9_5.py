"""Phase 9.5 verification criteria (docs/claude-code-workflow.md's Phase
9.5 prompt) -- the TheatreIntegration external/aggregator lock (design
§5.7, v18):

a. hold_seats returns a conflict -- Redis lock released, 409 with the
   correct conflicting seat IDs.
b. hold_seats times out/5xx -- Redis lock released, 503, circuit breaker
   trips after repeated failures.
c. confirm_hold fails after a successful hold and payment -- the Outbox
   retries it independently, booking stays CONFIRMED throughout.
d. release_hold fails on cancel and on sweep expiry -- the Outbox
   retries it, booking/seat state is still correctly CANCELLED/EXPIRED
   regardless.
e. theatre_hold_id is null on confirm/cancel (pre-v18 booking) -- the
   call is skipped cleanly, no Outbox row ever created.

Tests (a)/(b)'s HTTP-status-code assertions need the *live*, HTTP-wired
booking process actually returning 409/503 -- MockTheatreIntegration's
hold_mode is env-var-controlled (THEATRE_MOCK_HOLD_MODE, main.py) for
exactly this, and restart_booking_with_hold_mode below stops/restarts
the real process with it set, the same automated-restart technique
test_phase6.py already uses for the sweep workers (not a manual
operator step like test_phase5's payment-down test -- there's no real
theatre API here to actually fail on demand otherwise).

Tests (c)/(d) construct TheatreOutboxRelayWorker/ReconciliationSweepWorker
directly against the real booking_db (same technique test_phase6.py uses
for ReconciliationSweepWorker) rather than through the live process --
what matters there is Outbox retry behavior and resulting DB state, not
a specific HTTP status code, and direct construction is what lets a test
control confirm_hold_should_fail/release_hold_should_fail independently
of hold_seats' own behavior.
"""
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import psycopg2.extras
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BOOKING_DIR = os.path.join(REPO_ROOT, "services", "booking")
sys.path.insert(0, BOOKING_DIR)
from adapters.mock_theatre_integration import MockTheatreIntegration  # noqa: E402
from adapters.postgres_outbox_repository import PostgresOutboxRepository  # noqa: E402
from adapters.reconciliation_sweep import ReconciliationSweepWorker  # noqa: E402
from adapters.redis_seat_locker import lock_key  # noqa: E402
from adapters.theatre_outbox_relay import TheatreOutboxRelayWorker  # noqa: E402

ROUTING_BASE = "http://localhost:8000"
BOOKING_DIRECT_BASE = "http://localhost:8003"
BOOKING_DB_URL = os.environ.get(
    "BOOKING_DATABASE_URL",
    "postgresql://movieticket:movieticket_dev_password@localhost:5433/booking_db",
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6380")


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


@pytest.fixture
def booking_db():
    conn = psycopg2.connect(BOOKING_DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def redis_client():
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        client.close()


# --- shared helpers (same shape as test_phase5.py's, duplicated per this
# codebase's per-test-file convention -- no shared conftest/helpers module
# exists) ---


def unique(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


def new_admin() -> str:
    return str(uuid.uuid4())


def make_screen(routing) -> tuple[str, str]:
    theatres = routing.get("/theatre/theatres").json()
    city_id = theatres[0]["city_id"]
    theatre_resp = routing.post("/theatre/admin/theatres", json={"city_id": city_id, "name": unique("P9.5 Theatre")})
    assert theatre_resp.status_code == 201, theatre_resp.text
    theatre_id = theatre_resp.json()["id"]

    screen_resp = routing.post(f"/theatre/admin/theatres/{theatre_id}/screens", json={"name": "Screen 1"})
    assert screen_resp.status_code == 201, screen_resp.text
    return theatre_id, screen_resp.json()["id"]


def make_seat(label: str, x: float, y: float, seat_type: str = "STANDARD", price_multiplier: float = 1.0) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "label": label,
        "x": x,
        "y": y,
        "seat_type": seat_type,
        "price_multiplier": price_multiplier,
    }


def publish_layout(routing, screen_id: str, seats: list[dict]) -> dict:
    draft_resp = routing.post(
        "/theatre/admin/seat-layouts/draft", json={"screen_id": screen_id, "name": unique("Layout"), "seats": seats}
    )
    assert draft_resp.status_code == 201, draft_resp.text
    draft = draft_resp.json()

    admin = new_admin()
    lock_resp = routing.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/lock", headers={"X-Admin-User-Id": admin})
    assert lock_resp.status_code == 200, lock_resp.text

    publish_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft['id']}/publish", headers={"X-Admin-User-Id": admin}
    )
    assert publish_resp.status_code == 200, publish_resp.text
    return publish_resp.json()


def create_showtime(routing, screen_id: str, base_price: float = 100.0, movie_title: str = "P9.5 Movie") -> dict:
    body = {
        "movie_id": str(uuid.uuid4()),
        "movie_title": movie_title,
        "screen_id": screen_id,
        "start_time": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "base_price": base_price,
    }
    resp = routing.post("/theatre/admin/showtimes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_showtime_with_seats(routing, seats: list[dict], base_price: float = 100.0) -> dict:
    _theatre_id, screen_id = make_screen(routing)
    publish_layout(routing, screen_id, seats)
    return create_showtime(routing, screen_id, base_price=base_price)


def showtime_seat_ids_by_label(booking_db, showtime_id: str) -> dict[str, str]:
    with booking_db.cursor() as cur:
        cur.execute("SELECT id, label FROM showtime_seat WHERE showtime_id = %s", (showtime_id,))
        return {row["label"]: str(row["id"]) for row in cur.fetchall()}


def create_booking(routing, showtime_id: str, seat_ids: list[str], user_id: str = None) -> httpx.Response:
    return routing.post(
        "/booking/bookings",
        json={"showtime_id": showtime_id, "seat_ids": seat_ids, "user_id": user_id or str(uuid.uuid4())},
    )


def pay(routing, booking_id: str, amount: float) -> str:
    resp = routing.post("/payment/payments", json={"booking_id": booking_id, "amount": amount})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- live-process restart helper for tests (a)/(b) ---


def _find_booking_pids() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "uvicorn main:app --host 0.0.0.0 --port 8003"], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split() if p.strip()]


def _wait_for_booking_health(expected_hold_mode: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{BOOKING_DIRECT_BASE}/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.3)
    raise TimeoutError(f"booking service did not become healthy (expected hold_mode={expected_hold_mode})")


def _start_booking(hold_mode: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = BOOKING_DB_URL
    env["REDIS_URL"] = REDIS_URL
    env["PAYMENT_SERVICE_URL"] = "http://localhost:8004"
    env["PYTHONPATH"] = REPO_ROOT
    env["THEATRE_MOCK_HOLD_MODE"] = hold_mode
    log_path = os.path.join(REPO_ROOT, "logs", f"booking-restarted-{hold_mode}.log")
    subprocess.Popen(
        ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"],  # no --reload: a stable PID for this test
        cwd=BOOKING_DIR,
        env=env,
        stdout=open(log_path, "a"),
        stderr=subprocess.STDOUT,
    )
    _wait_for_booking_health(hold_mode)


@pytest.fixture
def booking_restarted_with_hold_mode():
    """Stops the dev.sh-managed booking process (THEATRE_MOCK_HOLD_MODE=
    success) for this test's duration and restarts it with whatever
    hold_mode the test requests, then restarts it back to "success"
    afterward -- same automated stop/restart convention test_phase6.py
    uses for the sweep workers."""
    state = {}

    def _activate(hold_mode: str) -> None:
        pids = _find_booking_pids()
        for pid in pids:
            os.kill(pid, signal.SIGTERM)
        if pids:
            time.sleep(1)
        state["had_external"] = bool(pids)
        _start_booking(hold_mode)

    yield _activate

    pids = _find_booking_pids()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    if pids:
        time.sleep(1)
    _start_booking("success")


# --- live-process restart helpers for tests (c)/(d): the Outbox relay
# and reconciliation sweep workers, so a test's own directly-constructed
# instances don't race the dev.sh-managed ones for the same advisory
# lock / the same due Outbox rows ---


def _find_outbox_relay_pids() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "adapters.theatre_outbox_relay"], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split() if p.strip()]


@pytest.fixture
def stop_external_outbox_relay_workers():
    pids = _find_outbox_relay_pids()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    if pids:
        time.sleep(1)

    yield

    if pids:
        env = os.environ.copy()
        env["DATABASE_URL"] = BOOKING_DB_URL
        env["PYTHONPATH"] = REPO_ROOT
        log_path = os.path.join(REPO_ROOT, "logs", "theatre-outbox-relay-restarted.log")
        for _ in pids:
            subprocess.Popen(
                [sys.executable, "-m", "adapters.theatre_outbox_relay"],
                cwd=BOOKING_DIR,
                env=env,
                stdout=open(log_path, "a"),
                stderr=subprocess.STDOUT,
            )
        time.sleep(1)


def _find_sweep_pids() -> list[int]:
    out = subprocess.run(["pgrep", "-f", "adapters.reconciliation_sweep"], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split() if p.strip()]


@pytest.fixture
def stop_external_sweep_workers():
    pids = _find_sweep_pids()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    if pids:
        time.sleep(1)

    yield

    if pids:
        env = os.environ.copy()
        env["DATABASE_URL"] = BOOKING_DB_URL
        env["PYTHONPATH"] = REPO_ROOT
        log_path = os.path.join(REPO_ROOT, "logs", "reconciliation-sweep-restarted.log")
        for _ in pids:
            subprocess.Popen(
                [sys.executable, "-m", "adapters.reconciliation_sweep"],
                cwd=BOOKING_DIR,
                env=env,
                stdout=open(log_path, "a"),
                stderr=subprocess.STDOUT,
            )
        time.sleep(1)


# --- (a) hold_seats conflict ---


def test_hold_seats_conflict_releases_redis_lock_and_returns_409(
    routing, booking_db, redis_client, booking_restarted_with_hold_mode
):
    booking_restarted_with_hold_mode("conflict")

    seats = [make_seat("A1", 0, 0), make_seat("A2", 1, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=80.0)
    seat_ids_by_label = showtime_seat_ids_by_label(booking_db, showtime["id"])
    seat_ids = [seat_ids_by_label["A1"], seat_ids_by_label["A2"]]

    resp = create_booking(routing, showtime["id"], seat_ids)
    assert resp.status_code == 409, resp.text
    body = resp.json()["detail"]
    assert sorted(body["conflicting_seat_ids"]) == sorted(seat_ids), body

    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM booking WHERE showtime_id = %s", (showtime["id"],))
        assert cur.fetchone()["c"] == 0, "no booking row should exist after a theatre-side conflict"

    # The Redis lock taken in step 1 (§5.1) must have been released
    # (compensated, §5.7's saga) once step 2's external hold came back
    # as a conflict -- otherwise these seats would be stuck unbookable
    # even after the theatre-side conflict clears.
    for seat_id in seat_ids:
        assert redis_client.get(lock_key(showtime["id"], seat_id)) is None, (
            f"seat {seat_id}'s Redis lock was not released after the theatre-side conflict"
        )

    # Proof the lock really was released, not just absent for some other
    # reason: switch back to success and the same seats book cleanly.
    booking_restarted_with_hold_mode("success")
    retry_resp = create_booking(routing, showtime["id"], seat_ids)
    assert retry_resp.status_code == 201, retry_resp.text


# --- (b) hold_seats timeout/5xx ---


def test_hold_seats_timeout_returns_503_and_releases_redis_lock(
    routing, booking_db, redis_client, booking_restarted_with_hold_mode
):
    booking_restarted_with_hold_mode("timeout")

    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=80.0)
    seat_ids_by_label = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids_by_label["A1"]

    resp = create_booking(routing, showtime["id"], [a1])
    assert resp.status_code == 503, resp.text

    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM booking WHERE showtime_id = %s", (showtime["id"],))
        assert cur.fetchone()["c"] == 0

    assert redis_client.get(lock_key(showtime["id"], a1)) is None, (
        "Redis lock must be released after a theatre API timeout, same as a conflict"
    )


def test_theatre_circuit_breaker_opens_after_consecutive_failures():
    """Unit-level, no service control needed -- same pattern as
    test_phase5.py's test_payment_circuit_breaker_opens_after_consecutive_failures,
    applied to the theatre integration's own breaker instance."""
    from adapters.circuit_breaker import CircuitBreaker
    from domain.theatre_integration import TheatreIntegrationUnavailable

    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=30.0, trips_on=TheatreIntegrationUnavailable)
    theatre = MockTheatreIntegration(hold_mode="timeout", breaker=breaker)

    for _ in range(2):
        with pytest.raises(TheatreIntegrationUnavailable):
            theatre.hold_seats(str(uuid.uuid4()), [str(uuid.uuid4())], hold_duration_seconds=600)

    assert breaker.is_open, "circuit must open after failure_threshold consecutive failures"

    started = time.monotonic()
    with pytest.raises(TheatreIntegrationUnavailable):
        theatre.hold_seats(str(uuid.uuid4()), [str(uuid.uuid4())], hold_duration_seconds=600)
    elapsed = time.monotonic() - started
    assert elapsed < 0.05, "an open circuit must reject immediately, with no simulated-timeout delay"


# --- (c) confirm_hold fails after a successful hold + payment ---


def test_confirm_hold_failure_retries_via_outbox_without_affecting_confirmed_status(
    routing, booking_db, stop_external_outbox_relay_workers
):
    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=90.0)
    seat_ids_by_label = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids_by_label["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    assert create_resp.status_code == 201, create_resp.text
    booking_id = create_resp.json()["id"]

    payment_id = pay(routing, booking_id, 90.0)
    confirm_resp = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["status"] == "CONFIRMED"

    with booking_db.cursor() as cur:
        cur.execute("SELECT * FROM pending_theatre_call WHERE booking_id = %s", (booking_id,))
        outbox_row = cur.fetchone()
    assert outbox_row is not None, "confirm() must enqueue a CONFIRM_HOLD outbox row in the same transaction"
    assert outbox_row["call_type"] == "CONFIRM_HOLD"
    assert outbox_row["status"] == "PENDING"
    assert outbox_row["attempts"] == 0

    failing_relay = TheatreOutboxRelayWorker(
        database_url=BOOKING_DB_URL,
        theatre=MockTheatreIntegration(confirm_hold_should_fail=True),
        backoff_base_seconds=0.01,  # trivial backoff -- keeps the row due across repeated passes in this test
    )
    for _ in range(3):
        failing_relay.run_one_relay_pass()
        time.sleep(0.05)

    with booking_db.cursor() as cur:
        cur.execute("SELECT * FROM pending_theatre_call WHERE id = %s", (outbox_row["id"],))
        retried_row = cur.fetchone()
    assert retried_row["status"] == "PENDING", "must still be retrying -- max_attempts (5) not yet exhausted"
    assert retried_row["attempts"] == 3, "each failing pass must bump the attempt counter"
    assert retried_row["last_error"], "the failure must be recorded for ops visibility (§13 manual reconciliation)"

    # The booking's CONFIRMED status was already correct before the
    # relay ever got involved, and must stay that way regardless of how
    # many times confirm_hold itself fails.
    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        assert cur.fetchone()["status"] == "CONFIRMED"

    # Once the theatre side recovers, the *same* outbox row succeeds --
    # proof this is genuine retry-until-success, not retry-forever.
    succeeding_relay = TheatreOutboxRelayWorker(database_url=BOOKING_DB_URL, theatre=MockTheatreIntegration())
    succeeding_relay.run_one_relay_pass()

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM pending_theatre_call WHERE id = %s", (outbox_row["id"],))
        assert cur.fetchone()["status"] == "DONE"


# --- (d) release_hold fails, on both cancel and sweep expiry ---


def test_release_hold_failure_on_cancel_retries_via_outbox_with_cancelled_state_intact(
    routing, booking_db, stop_external_outbox_relay_workers
):
    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=70.0)
    seat_ids_by_label = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids_by_label["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    assert create_resp.status_code == 201, create_resp.text
    booking_id = create_resp.json()["id"]

    cancel_resp = routing.delete(f"/booking/bookings/{booking_id}")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "CANCELLED"

    with booking_db.cursor() as cur:
        cur.execute("SELECT * FROM pending_theatre_call WHERE booking_id = %s", (booking_id,))
        outbox_row = cur.fetchone()
    assert outbox_row is not None and outbox_row["call_type"] == "RELEASE_HOLD"

    failing_relay = TheatreOutboxRelayWorker(
        database_url=BOOKING_DB_URL,
        theatre=MockTheatreIntegration(release_hold_should_fail=True),
        backoff_base_seconds=0.01,
    )
    for _ in range(2):
        failing_relay.run_one_relay_pass()
        time.sleep(0.05)

    with booking_db.cursor() as cur:
        cur.execute("SELECT status, attempts FROM pending_theatre_call WHERE id = %s", (outbox_row["id"],))
        row = cur.fetchone()
    assert row["status"] == "PENDING"
    assert row["attempts"] == 2

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        assert cur.fetchone()["status"] == "CANCELLED", "cancellation must hold regardless of release_hold's failures"
        cur.execute("SELECT status FROM showtime_seat WHERE id = %s", (a1,))
        assert cur.fetchone()["status"] == "AVAILABLE", "seat must still be released back to AVAILABLE"

    succeeding_relay = TheatreOutboxRelayWorker(database_url=BOOKING_DB_URL, theatre=MockTheatreIntegration())
    succeeding_relay.run_one_relay_pass()
    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM pending_theatre_call WHERE id = %s", (outbox_row["id"],))
        assert cur.fetchone()["status"] == "DONE"


def test_release_hold_failure_on_sweep_expiry_retries_via_outbox_with_expired_state_intact(
    routing, booking_db, stop_external_outbox_relay_workers, stop_external_sweep_workers
):
    """Same as the cancel-path test above, but for the reconciliation
    sweep's own expiry path (§5.4) -- the sweep must enqueue RELEASE_HOLD
    too, not just the cancel endpoint (this phase's item 5)."""
    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=70.0)
    seat_ids_by_label = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids_by_label["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    assert create_resp.status_code == 201, create_resp.text
    booking_id = create_resp.json()["id"]

    # Backdate expires_at into the past -- same technique test_phase5.py/
    # test_phase6.py use (direct DB backdating), no test-only
    # clock-mocking hook in production code.
    with booking_db.cursor() as cur:
        cur.execute("UPDATE booking SET expires_at = now() - interval '1 minute' WHERE id = %s", (booking_id,))
    booking_db.commit()

    sweep = ReconciliationSweepWorker(database_url=BOOKING_DB_URL)
    assert sweep.try_become_active(), "no other sweep instance should hold the advisory lock during this test"
    try:
        expired_count = sweep.run_one_sweep_pass()
    finally:
        sweep.release_active()
    assert expired_count >= 1

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        assert cur.fetchone()["status"] == "EXPIRED"
        cur.execute("SELECT * FROM pending_theatre_call WHERE booking_id = %s", (booking_id,))
        outbox_row = cur.fetchone()
    assert outbox_row is not None and outbox_row["call_type"] == "RELEASE_HOLD", (
        "the sweep's own expiry path must enqueue a RELEASE_HOLD outbox entry, same as cancel()"
    )

    failing_relay = TheatreOutboxRelayWorker(
        database_url=BOOKING_DB_URL,
        theatre=MockTheatreIntegration(release_hold_should_fail=True),
        backoff_base_seconds=0.01,
    )
    failing_relay.run_one_relay_pass()
    time.sleep(0.05)
    failing_relay.run_one_relay_pass()
    time.sleep(0.05)

    with booking_db.cursor() as cur:
        cur.execute("SELECT status, attempts FROM pending_theatre_call WHERE id = %s", (outbox_row["id"],))
        row = cur.fetchone()
    assert row["status"] == "PENDING"
    assert row["attempts"] == 2
    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        assert cur.fetchone()["status"] == "EXPIRED", "expiry must hold regardless of release_hold's failures"

    succeeding_relay = TheatreOutboxRelayWorker(database_url=BOOKING_DB_URL, theatre=MockTheatreIntegration())
    succeeding_relay.run_one_relay_pass()
    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM pending_theatre_call WHERE id = %s", (outbox_row["id"],))
        assert cur.fetchone()["status"] == "DONE"


# --- (e) theatre_hold_id is null (pre-v18 booking compatibility) ---


def test_null_theatre_hold_id_skips_confirm_and_cancel_outbox_enqueue_cleanly(routing, booking_db):
    """Bookings created before this feature existed have theatre_hold_id
    = NULL (no backfill, §13) -- confirm/cancel must skip the Outbox
    enqueue entirely for them, not error or enqueue a useless call."""
    seats = [make_seat("A1", 0, 0), make_seat("A2", 1, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=50.0)
    seat_ids_by_label = showtime_seat_ids_by_label(booking_db, showtime["id"])

    def insert_legacy_pending_booking(label: str) -> str:
        seat_id = seat_ids_by_label[label]
        booking_id = str(uuid.uuid4())
        with booking_db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO booking
                    (id, idempotency_key, user_id, showtime_id, movie_title, seat_labels, price_paid, status, expires_at, theatre_hold_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', now() + interval '10 minutes', NULL)
                """,
                (booking_id, unique("legacy-key"), str(uuid.uuid4()), showtime["id"], "Legacy Movie", label, 50.0),
            )
            cur.execute(
                "UPDATE showtime_seat SET status = 'LOCKED', locked_by_booking_id = %s, "
                "lock_expires_at = now() + interval '10 minutes' WHERE id = %s",
                (booking_id, seat_id),
            )
        booking_db.commit()
        return booking_id

    confirm_booking_id = insert_legacy_pending_booking("A1")
    payment_id = pay(routing, confirm_booking_id, 50.0)
    confirm_resp = routing.post(f"/booking/bookings/{confirm_booking_id}/confirm", json={"payment_id": payment_id})
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["status"] == "CONFIRMED"
    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM pending_theatre_call WHERE booking_id = %s", (confirm_booking_id,))
        assert cur.fetchone()["c"] == 0, "confirm() must not enqueue anything for a NULL theatre_hold_id"

    cancel_booking_id = insert_legacy_pending_booking("A2")
    cancel_resp = routing.delete(f"/booking/bookings/{cancel_booking_id}")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "CANCELLED"
    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM pending_theatre_call WHERE booking_id = %s", (cancel_booking_id,))
        assert cur.fetchone()["c"] == 0, "cancel() must not enqueue anything for a NULL theatre_hold_id"
