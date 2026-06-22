"""Phase 6 verification criteria -- design §5.4's own required-test list,
used verbatim (implementation-plan.md Phase 6 reuses it directly):

1. Kill the active instance mid-batch, confirm a standby takes over
   within one poll interval, no half-processed booking.
2. Start three replicas simultaneously, confirm exactly one acquires the
   advisory lock.
3. Race a concurrent payment-confirm against the sweep's selection of
   the same booking -- confirm confirmation always wins.
4. Re-run the sweep immediately after a successful pass, confirm zero
   rows affected.
5. Seed a backlog of expired PENDING bookings, confirm it drains in
   bounded batches without degrading the booking API's normal request
   path.

These tests construct and fully control their own
ReconciliationSweepWorker instances against the real booking_db, and
therefore need the database-wide Postgres advisory lock (§5.4) free of
any *other* holder for the duration of the module -- the dev.sh-managed
reconciliation-sweep-1/2 processes hold the same global lock key and
would otherwise race these tests for it. The autouse module fixture
below stops them for this module's duration and restarts them
afterward, the same way earlier phases stopped/restarted real
booking/payment service processes for their own fail-closed tests.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import psycopg2.extras
import pytest
from psycopg2.extras import execute_values

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BOOKING_DIR = os.path.join(REPO_ROOT, "services", "booking")
sys.path.insert(0, BOOKING_DIR)
from adapters.reconciliation_sweep import ReconciliationSweepWorker  # noqa: E402

ROUTING_BASE = "http://localhost:8000"
BOOKING_DB_URL = os.environ.get(
    "BOOKING_DATABASE_URL",
    "postgresql://movieticket:movieticket_dev_password@localhost:5433/booking_db",
)


def _find_sweep_pids() -> list[int]:
    # dev.sh runs workers via `python -m adapters.reconciliation_sweep`
    # (scripts/dev.sh's start_worker, Phase 9.5) rather than a file path
    # -- `python -m pkg.module` resolves cross-module imports (adapters.X,
    # domain.X) the new theatre-integration workers need, which `python
    # path/to/file.py` does not (it sets sys.path[0] to the file's own
    # directory, not cwd). Match the dotted form pgrep actually sees.
    out = subprocess.run(["pgrep", "-f", "adapters.reconciliation_sweep"], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split() if p.strip()]


@pytest.fixture(scope="module", autouse=True)
def stop_external_sweep_workers():
    pids = _find_sweep_pids()
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    if pids:
        time.sleep(1)
        print(f"\n[setup] stopped {len(pids)} externally-managed sweep worker(s) for this module: {pids}")

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
        print(f"[teardown] restarted {len(pids)} sweep worker(s)")


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


def unique(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


def new_admin() -> str:
    return str(uuid.uuid4())


def make_screen(routing) -> tuple[str, str]:
    theatres = routing.get("/theatre/theatres").json()
    city_id = theatres[0]["city_id"]
    theatre_resp = routing.post("/theatre/admin/theatres", json={"city_id": city_id, "name": unique("Phase6 Theatre")})
    assert theatre_resp.status_code == 201, theatre_resp.text
    theatre_id = theatre_resp.json()["id"]
    screen_resp = routing.post(f"/theatre/admin/theatres/{theatre_id}/screens", json={"name": "Screen 1"})
    assert screen_resp.status_code == 201, screen_resp.text
    return theatre_id, screen_resp.json()["id"]


def make_seat(label: str, x: float, y: float, seat_type: str = "STANDARD", price_multiplier: float = 1.0) -> dict:
    return {"id": str(uuid.uuid4()), "label": label, "x": x, "y": y, "seat_type": seat_type, "price_multiplier": price_multiplier}


def publish_layout(routing, screen_id: str, seats: list[dict]) -> dict:
    draft_resp = routing.post("/theatre/admin/seat-layouts/draft", json={"screen_id": screen_id, "name": unique("Layout"), "seats": seats})
    assert draft_resp.status_code == 201, draft_resp.text
    draft = draft_resp.json()
    admin = new_admin()
    lock_resp = routing.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/lock", headers={"X-Admin-User-Id": admin})
    assert lock_resp.status_code == 200, lock_resp.text
    publish_resp = routing.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/publish", headers={"X-Admin-User-Id": admin})
    assert publish_resp.status_code == 200, publish_resp.text
    return publish_resp.json()


def create_showtime(routing, screen_id: str, base_price: float = 100.0, movie_title: str = "Test Movie") -> dict:
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


def make_showtime_with_seats(routing, seats: list[dict], base_price: float = 100.0, movie_title: str = "Test Movie") -> dict:
    _theatre_id, screen_id = make_screen(routing)
    publish_layout(routing, screen_id, seats)
    return create_showtime(routing, screen_id, base_price=base_price, movie_title=movie_title)


def showtime_seat_ids_by_label(booking_db, showtime_id: str) -> dict[str, str]:
    with booking_db.cursor() as cur:
        cur.execute("SELECT id, label FROM showtime_seat WHERE showtime_id = %s", (showtime_id,))
        return {row["label"]: str(row["id"]) for row in cur.fetchall()}


def create_booking(routing, showtime_id: str, seat_ids: list[str], user_id: str = None) -> httpx.Response:
    return routing.post("/booking/bookings", json={"showtime_id": showtime_id, "seat_ids": seat_ids, "user_id": user_id or str(uuid.uuid4())})


def pay(routing, booking_id: str, amount: float) -> str:
    resp = routing.post("/payment/payments", json={"booking_id": booking_id, "amount": amount})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def backdate_booking_and_seats(booking_db, booking_id: str, minutes_ago: float) -> None:
    with booking_db.cursor() as cur:
        cur.execute("UPDATE booking SET expires_at = now() - INTERVAL '1 minute' * %s WHERE id = %s", (minutes_ago, booking_id))
        cur.execute(
            "UPDATE showtime_seat SET lock_expires_at = now() - INTERVAL '1 minute' * %s WHERE locked_by_booking_id = %s",
            (minutes_ago, booking_id),
        )
    booking_db.commit()


def seed_backlog(booking_db, n: int, showtime_id: str) -> list[str]:
    """Direct SQL seeding (not via the API) -- this test exercises the
    sweep worker's batching/draining, not booking creation, so synthetic
    rows are appropriate and far faster than n real API calls."""
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    booking_ids = [str(uuid.uuid4()) for _ in range(n)]
    booking_rows = [
        (bid, f"backlog-{bid}", str(uuid.uuid4()), showtime_id, "Backlog Movie", "Z1", 42.0, "PENDING", expires_at)
        for bid in booking_ids
    ]
    seat_rows = [
        (str(uuid.uuid4()), showtime_id, str(uuid.uuid4()), "Z1", 0.0, 0.0, "STANDARD", 42.0, "LOCKED", bid, expires_at)
        for bid in booking_ids
    ]
    with booking_db.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO booking (id, idempotency_key, user_id, showtime_id, movie_title, seat_labels, price_paid, status, expires_at) VALUES %s",
            booking_rows,
        )
        execute_values(
            cur,
            "INSERT INTO showtime_seat (id, showtime_id, seat_template_id, label, position_x, position_y, seat_type, price, status, locked_by_booking_id, lock_expires_at) VALUES %s",
            seat_rows,
        )
    booking_db.commit()
    return booking_ids


# --- 1. kill active instance mid-batch, standby takes over, no half-processed booking ---

def test_kill_active_instance_mid_batch_standby_takes_over_no_half_processed_booking(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=40.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    assert create_resp.status_code == 201, create_resp.text
    booking_id = create_resp.json()["id"]
    backdate_booking_and_seats(booking_db, booking_id, minutes_ago=15)

    worker_a = ReconciliationSweepWorker(database_url=BOOKING_DB_URL, poll_interval_seconds=2, lock_retry_seconds=1)
    worker_b = ReconciliationSweepWorker(database_url=BOOKING_DB_URL, poll_interval_seconds=2, lock_retry_seconds=1)

    assert worker_a.try_become_active() is True

    ready = threading.Event()
    proceed = threading.Event()

    def pause_hook():
        ready.set()
        proceed.wait(timeout=5)

    worker_a._after_booking_update_hook = pause_hook

    pass_outcome = {}

    def run_pass_a():
        try:
            pass_outcome["count"] = worker_a.run_one_sweep_pass()
        except Exception as exc:  # connection will be force-closed mid-flight
            pass_outcome["error"] = exc

    thread_a = threading.Thread(target=run_pass_a)
    thread_a.start()
    assert ready.wait(timeout=5), "worker A never reached the mid-transaction pause point"

    # Simulate the active instance actually crashing mid-batch: force-close
    # both its in-flight sweep connection and its advisory-lock connection
    # for real, not a mocked signal.
    worker_a._last_sweep_conn.close()
    worker_a._lock_conn.close()
    proceed.set()
    thread_a.join(timeout=5)
    print(f"\n[kill mid-batch] worker A's aborted pass outcome: {pass_outcome}")

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        assert cur.fetchone()["status"] == "PENDING", "the aborted (uncommitted) sweep pass must not leave the booking EXPIRED"
        cur.execute("SELECT status FROM showtime_seat WHERE id::text = %s", (a1,))
        assert cur.fetchone()["status"] == "LOCKED", "no half-processed booking: seat must still be LOCKED, matching the still-PENDING booking"

    worker_b_thread = threading.Thread(target=worker_b.run_forever, daemon=True)
    started_waiting = time.monotonic()
    worker_b_thread.start()
    deadline = started_waiting + worker_b._poll_interval_seconds + 3
    became_active_after = None
    while time.monotonic() < deadline:
        if worker_b.is_active:
            became_active_after = time.monotonic() - started_waiting
            break
        time.sleep(0.1)
    print(f"[kill mid-batch] standby became active after {became_active_after}s (poll_interval={worker_b._poll_interval_seconds}s)")
    assert became_active_after is not None, "standby must take over within roughly one poll interval"
    assert became_active_after <= worker_b._poll_interval_seconds + 2, "failover took longer than ~one poll interval"

    time.sleep(1.5)  # let the now-active standby's first pass actually complete
    worker_b.stop()

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        assert cur.fetchone()["status"] == "EXPIRED"
        cur.execute("SELECT status, locked_by_booking_id FROM showtime_seat WHERE id::text = %s", (a1,))
        seat_row = cur.fetchone()
        assert seat_row["status"] == "AVAILABLE"
        assert seat_row["locked_by_booking_id"] is None


# --- 2. three replicas started simultaneously, exactly one acquires the lock ---

def test_three_replicas_started_simultaneously_exactly_one_acquires_lock():
    workers = [ReconciliationSweepWorker(database_url=BOOKING_DB_URL, poll_interval_seconds=5, lock_retry_seconds=5) for _ in range(3)]
    results = [None, None, None]
    barrier = threading.Barrier(3)

    def attempt(i):
        barrier.wait()
        results[i] = workers[i].try_become_active()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    print(f"\n[3-replica race] results={results}")
    winners = sum(1 for r in results if r)
    assert winners == 1, f"exactly one of 3 simultaneous replicas must acquire the advisory lock, got {results}"

    for w in workers:
        w.stop()


# --- 3. confirm always wins the race against sweep selection ---

def test_concurrent_confirm_wins_race_against_sweep_selection(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=70.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    booking = create_resp.json()
    booking_id = booking["id"]
    payment_id = pay(routing, booking_id, booking["price_paid"])

    backdate_booking_and_seats(booking_db, booking_id, minutes_ago=15)

    worker = ReconciliationSweepWorker(database_url=BOOKING_DB_URL)
    assert worker.try_become_active()

    confirm_result = {}

    def race_confirm():
        resp = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
        confirm_result["status_code"] = resp.status_code
        confirm_result["body"] = resp.json()

    worker._after_select_hook = race_confirm

    expired_count = worker.run_one_sweep_pass()
    worker.stop()

    print(f"\n[confirm-vs-sweep race] sweep expired_count={expired_count} confirm_result={confirm_result}")
    assert confirm_result.get("status_code") == 200, confirm_result
    assert confirm_result["body"]["status"] == "CONFIRMED"

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM booking WHERE id = %s", (booking_id,))
        final_status = cur.fetchone()["status"]
    assert final_status == "CONFIRMED", "confirmation must always win the race against sweep selection"

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM showtime_seat WHERE id::text = %s", (a1,))
        assert cur.fetchone()["status"] == "BOOKED"


# --- 4. re-run immediately after success affects zero rows ---

def test_rerun_immediately_after_success_affects_zero_rows(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=30.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    booking_id = create_resp.json()["id"]
    backdate_booking_and_seats(booking_db, booking_id, minutes_ago=15)

    worker = ReconciliationSweepWorker(database_url=BOOKING_DB_URL)
    first = worker.run_one_sweep_pass()
    second = worker.run_one_sweep_pass()
    print(f"\n[re-run] first_pass={first} second_pass={second}")

    assert first >= 1
    assert second == 0, "re-running immediately after a successful pass must affect zero rows"


# --- 5. backlog drains in bounded batches without degrading the booking API ---

def test_backlog_drains_in_bounded_batches_without_degrading_booking_api(routing, booking_db):
    backlog_size = 1200
    batch_size = 200
    fake_showtime_id = str(uuid.uuid4())
    booking_ids = seed_backlog(booking_db, backlog_size, fake_showtime_id)

    real_seats = [make_seat("A1", 0, 0)]
    real_showtime = make_showtime_with_seats(routing, real_seats, base_price=25.0)
    real_seat_ids = showtime_seat_ids_by_label(booking_db, real_showtime["id"])
    real_a1 = real_seat_ids["A1"]

    api_latencies: list[float] = []
    api_errors: list[str] = []
    stop_polling = threading.Event()

    def poll_booking_api():
        while not stop_polling.is_set():
            started = time.monotonic()
            try:
                resp = create_booking(routing, real_showtime["id"], [real_a1])
                elapsed = time.monotonic() - started
                if resp.status_code != 201:
                    api_errors.append(f"create -> {resp.status_code}: {resp.text}")
                else:
                    api_latencies.append(elapsed)
                    del_resp = routing.delete(f"/booking/bookings/{resp.json()['id']}")
                    if del_resp.status_code != 200:
                        api_errors.append(f"cancel -> {del_resp.status_code}: {del_resp.text}")
            except Exception as exc:
                api_errors.append(str(exc))
            time.sleep(0.05)

    poll_thread = threading.Thread(target=poll_booking_api, daemon=True)
    poll_thread.start()

    worker = ReconciliationSweepWorker(database_url=BOOKING_DB_URL, batch_size=batch_size)
    pass_counts = []
    while True:
        n = worker.run_one_sweep_pass()
        if n == 0:
            break
        pass_counts.append(n)

    stop_polling.set()
    poll_thread.join(timeout=5)

    avg_latency = (sum(api_latencies) / len(api_latencies)) if api_latencies else 0.0
    max_latency = max(api_latencies) if api_latencies else 0.0
    print(
        f"\n[backlog drain] backlog={backlog_size} batch_size={batch_size} passes={len(pass_counts)} "
        f"pass_counts={pass_counts}"
    )
    print(
        f"[backlog drain] concurrent booking API: {len(api_latencies)} create+cancel cycles ok, "
        f"{len(api_errors)} failed, avg_latency={avg_latency:.3f}s max_latency={max_latency:.3f}s"
    )

    assert sum(pass_counts) == backlog_size, f"the full backlog must drain, got {sum(pass_counts)} of {backlog_size}"
    assert all(c <= batch_size for c in pass_counts), f"every pass must be bounded by batch_size={batch_size}, got {pass_counts}"
    assert len(pass_counts) >= (backlog_size // batch_size), "a backlog this size must take multiple bounded passes, not one unbounded one"
    assert not api_errors, f"the booking API must not fail while the backlog drains: {api_errors[:5]}"
    assert api_latencies, "the polling thread must have completed at least one booking create+cancel cycle during the drain"
    assert max_latency < 2.0, f"booking API must not be degraded by the sweep's batched transactions, max latency was {max_latency:.3f}s"

    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM booking WHERE id::text = ANY(%s) AND status = 'EXPIRED'", (booking_ids,))
        assert cur.fetchone()["n"] == backlog_size


# --- supplementary (not one of §5.4's five required tests above) ---
#
# Phase 6 changed confirm()'s behavior (design v14): it no longer
# self-polices wall-clock expiry, since the sweep is now the sole timing
# authority. This re-demonstrates correctly, in this module's
# sweep-controlled environment, the scenario Phase 5's now-retired
# test_confirm_fails_after_hold_expires used to cover.

def test_confirm_succeeds_past_expiry_if_unswept_but_fails_once_swept(routing, booking_db):
    seats = [make_seat("A1", 0, 0), make_seat("A2", 1, 0)]
    showtime = make_showtime_with_seats(routing, seats, base_price=55.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1, a2 = seat_ids["A1"], seat_ids["A2"]

    # Part 1: expired but unswept -- confirm still succeeds (confirm beat
    # the sweep to the database simply because the sweep never ran).
    create_resp = create_booking(routing, showtime["id"], [a1])
    booking = create_resp.json()
    booking_id = booking["id"]
    payment_id = pay(routing, booking_id, booking["price_paid"])
    backdate_booking_and_seats(booking_db, booking_id, minutes_ago=11)

    confirm_resp = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["status"] == "CONFIRMED"

    # Part 2: a second, separately expired booking -- but this time the
    # sweep actually runs (and wins) first. Confirm must now fail.
    create_resp2 = create_booking(routing, showtime["id"], [a2])
    booking2 = create_resp2.json()
    booking2_id = booking2["id"]
    payment2_id = pay(routing, booking2_id, booking2["price_paid"])
    backdate_booking_and_seats(booking_db, booking2_id, minutes_ago=11)

    worker = ReconciliationSweepWorker(database_url=BOOKING_DB_URL)
    assert worker.try_become_active()
    swept = worker.run_one_sweep_pass()
    worker.stop()
    assert swept >= 1

    confirm_resp2 = routing.post(f"/booking/bookings/{booking2_id}/confirm", json={"payment_id": payment2_id})
    assert confirm_resp2.status_code == 409, confirm_resp2.text
    assert "expired" in confirm_resp2.text.lower()
