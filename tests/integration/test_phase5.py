"""Phase 5 verification criteria (implementation-plan.md):

- Happy-path: browse -> select seats -> create booking (PENDING) -> pay
  -> confirm -> seat status BOOKED, booking CONFIRMED, denormalized
  snapshot fields (movie_title, seat_labels, price_paid) correct.
- Conflict test: two attempts for the same seats -- one succeeds, one
  gets a clean 409 with the conflicting seat IDs.
- Expired-lock test: advance time past the booking's hold window
  (direct DB backdating, same technique as test_phase2.py's heartbeat
  backdating -- no test-only clock-mocking hook in production code),
  confirm a confirm-attempt fails appropriately rather than succeeding
  or crashing.
- Idempotency test: (a) replaying the *creation* request (same showtime,
  user, seat set) while the original is still live returns the same
  booking, not a new one or a conflict (§11.1 v12's whole point); (b)
  replaying *confirm* after it already succeeded returns the same
  CONFIRMED result without re-executing (§5.6).
- Payment-down test: confirm the circuit breaker trips and the booking
  stays safely PENDING rather than erroring destructively.

Explicitly NOT tested here (per the user's note): a "showtime-deletion
race" test. Showtime deactivation (v10) only flips is_active and never
touches SHOWTIME_SEAT/BOOKING state, so there is no check-then-act race
left to guard against -- that test was removed from this phase's scope
by design, not by oversight.

The payment-down test (test_confirm_fails_closed_when_payment_service_down)
requires the payment service process to actually be stopped for its
duration, same convention as test_phase3.py's fail-closed test -- it
self-skips if payment is reachable, with instructions in its docstring.
"""
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import psycopg2.extras
import pytest

ROUTING_BASE = "http://localhost:8000"
PAYMENT_DIRECT_BASE = "http://localhost:8004"
THEATRE_DB_URL = os.environ.get(
    "THEATRE_DATABASE_URL",
    "postgresql://movieticket:movieticket_dev_password@localhost:5433/theatre_db",
)
BOOKING_DB_URL = os.environ.get(
    "BOOKING_DATABASE_URL",
    "postgresql://movieticket:movieticket_dev_password@localhost:5433/booking_db",
)


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
    theatre_resp = routing.post(
        "/theatre/admin/theatres", json={"city_id": city_id, "name": unique("Phase5 Theatre")}
    )
    assert theatre_resp.status_code == 201, theatre_resp.text
    theatre_id = theatre_resp.json()["id"]

    screen_resp = routing.post(
        f"/theatre/admin/theatres/{theatre_id}/screens", json={"name": "Screen 1"}
    )
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
        "/theatre/admin/seat-layouts/draft",
        json={"screen_id": screen_id, "name": unique("Layout"), "seats": seats},
    )
    assert draft_resp.status_code == 201, draft_resp.text
    draft = draft_resp.json()

    admin = new_admin()
    lock_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft['id']}/lock", headers={"X-Admin-User-Id": admin}
    )
    assert lock_resp.status_code == 200, lock_resp.text

    publish_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft['id']}/publish", headers={"X-Admin-User-Id": admin}
    )
    assert publish_resp.status_code == 200, publish_resp.text
    return publish_resp.json()


def create_showtime(
    routing, screen_id: str, base_price: float = 100.0, movie_title: str = "Test Movie", start_time=None
) -> dict:
    body = {
        "movie_id": str(uuid.uuid4()),
        "movie_title": movie_title,
        "screen_id": screen_id,
        "start_time": (start_time or (datetime.now(timezone.utc) + timedelta(days=7))).isoformat(),
        "base_price": base_price,
    }
    resp = routing.post("/theatre/admin/showtimes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_showtime_with_seats(routing, seats: list[dict], base_price: float = 100.0, movie_title: str = "Test Movie") -> tuple[dict, dict]:
    """Returns (showtime, published_layout) -- published_layout['seats']
    gives the template seats; the actual bookable showtime_seat IDs must
    be looked up from booking_db since materialization mints fresh IDs."""
    _theatre_id, screen_id = make_screen(routing)
    layout = publish_layout(routing, screen_id, seats)
    showtime = create_showtime(routing, screen_id, base_price=base_price, movie_title=movie_title)
    return showtime, layout


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


def backdate_booking_and_seats(booking_db, booking_id: str, minutes_ago: float) -> None:
    with booking_db.cursor() as cur:
        cur.execute(
            "UPDATE booking SET expires_at = now() - INTERVAL '1 minute' * %s WHERE id = %s",
            (minutes_ago, booking_id),
        )
        cur.execute(
            "UPDATE showtime_seat SET lock_expires_at = now() - INTERVAL '1 minute' * %s WHERE locked_by_booking_id = %s",
            (minutes_ago, booking_id),
        )
    booking_db.commit()


# --- happy path ---

def test_happy_path_browse_select_pending_pay_confirm_booked(routing, booking_db):
    seats = [make_seat("A1", 0, 0), make_seat("A2", 1, 0, seat_type="PREMIUM", price_multiplier=1.5)]
    showtime, _layout = make_showtime_with_seats(routing, seats, base_price=100.0, movie_title="Dune Part Three")
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1, a2 = seat_ids["A1"], seat_ids["A2"]

    user_id = str(uuid.uuid4())
    create_resp = create_booking(routing, showtime["id"], [a1, a2], user_id=user_id)
    assert create_resp.status_code == 201, create_resp.text
    booking = create_resp.json()
    assert booking["status"] == "PENDING"
    assert booking["movie_title"] == "Dune Part Three"
    assert booking["seat_labels"] == "A1,A2"
    assert booking["price_paid"] == pytest.approx(100.0 + 150.0)
    booking_id = booking["id"]

    payment_id = pay(routing, booking_id, booking["price_paid"])

    confirm_resp = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
    assert confirm_resp.status_code == 200, confirm_resp.text
    confirmed = confirm_resp.json()
    assert confirmed["status"] == "CONFIRMED"

    get_resp = routing.get(f"/booking/bookings/{booking_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "CONFIRMED"

    with booking_db.cursor() as cur:
        cur.execute("SELECT label, status FROM showtime_seat WHERE id::text = ANY(%s)", ([a1, a2],))
        rows = {r["label"]: r["status"] for r in cur.fetchall()}
    assert rows == {"A1": "BOOKED", "A2": "BOOKED"}


# --- conflict: two attempts for the same seats ---

def test_two_booking_attempts_for_same_seat_one_succeeds_one_409(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime, _layout = make_showtime_with_seats(routing, seats)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(create_booking, routing, showtime["id"], [a1], user_a)
        fut_b = pool.submit(create_booking, routing, showtime["id"], [a1], user_b)
        resp_a, resp_b = fut_a.result(), fut_b.result()

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 409], (resp_a.status_code, resp_a.text, resp_b.status_code, resp_b.text)

    loser = resp_a if resp_a.status_code == 409 else resp_b
    assert loser.json()["detail"]["conflicting_seat_ids"] == [a1]


# --- expired lock ---

def test_confirm_fails_after_hold_expires(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime, _layout = make_showtime_with_seats(routing, seats, base_price=50.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    booking = create_resp.json()
    booking_id = booking["id"]
    payment_id = pay(routing, booking_id, booking["price_paid"])

    backdate_booking_and_seats(booking_db, booking_id, minutes_ago=11)

    confirm_resp = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
    assert confirm_resp.status_code == 409, confirm_resp.text
    assert "expired" in confirm_resp.text.lower()

    get_resp = routing.get(f"/booking/bookings/{booking_id}")
    assert get_resp.json()["status"] == "PENDING", "an expired-but-unswept booking is still nominally PENDING (Phase 6 sweep doesn't exist yet)"


# --- idempotency ---

def test_replaying_booking_creation_returns_same_live_booking(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime, _layout = make_showtime_with_seats(routing, seats)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]
    user_id = str(uuid.uuid4())

    first = create_booking(routing, showtime["id"], [a1], user_id=user_id)
    assert first.status_code == 201, first.text

    second = create_booking(routing, showtime["id"], [a1], user_id=user_id)
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"], "a replay of the same (user, showtime, seats) request must return the SAME booking, not a 409 or a new row"

    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM booking WHERE id = %s", (first.json()["id"],))
        assert cur.fetchone()["n"] == 1


def test_replaying_booking_creation_after_terminal_state_creates_a_fresh_booking(routing, booking_db):
    """The whole point of the partial unique index (§11.1 v12): once the
    first attempt reaches a terminal state, the same (user, showtime,
    seats) identity must be free to try again -- not permanently deduped."""
    seats = [make_seat("A1", 0, 0)]
    showtime, _layout = make_showtime_with_seats(routing, seats)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]
    user_id = str(uuid.uuid4())

    first = create_booking(routing, showtime["id"], [a1], user_id=user_id)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    cancel_resp = routing.delete(f"/booking/bookings/{first_id}")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "CANCELLED"

    second = create_booking(routing, showtime["id"], [a1], user_id=user_id)
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first_id, "a fresh attempt after a terminal (CANCELLED) booking must not be blocked"
    assert second.json()["status"] == "PENDING"


def test_replaying_confirm_returns_same_result_without_re_executing(routing, booking_db):
    seats = [make_seat("A1", 0, 0)]
    showtime, _layout = make_showtime_with_seats(routing, seats, base_price=80.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    booking = create_resp.json()
    booking_id = booking["id"]
    payment_id = pay(routing, booking_id, booking["price_paid"])

    first_confirm = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
    assert first_confirm.status_code == 200, first_confirm.text

    with booking_db.cursor() as cur:
        cur.execute("SELECT updated_at FROM booking WHERE id = %s", (booking_id,))
        updated_at_after_first = cur.fetchone()["updated_at"]

    second_confirm = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": payment_id})
    assert second_confirm.status_code == 200, second_confirm.text
    assert second_confirm.json()["status"] == "CONFIRMED"

    with booking_db.cursor() as cur:
        cur.execute("SELECT updated_at FROM booking WHERE id = %s", (booking_id,))
        updated_at_after_second = cur.fetchone()["updated_at"]
    assert updated_at_after_second == updated_at_after_first, "a replayed confirm must not re-execute the UPDATE"


# --- payment circuit breaker (unit-level, no service control needed) ---

def test_payment_circuit_breaker_opens_after_consecutive_failures():
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "booking"))
    from adapters.payment_client import CircuitBreaker, PaymentClient, PaymentServiceUnavailable

    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=30.0)
    # Nothing listens on this port -- every call is a transport error.
    client = PaymentClient(base_url="http://localhost:19", breaker=breaker)

    for _ in range(2):
        with pytest.raises(PaymentServiceUnavailable):
            client.get_payment(str(uuid.uuid4()))

    assert breaker.is_open, "circuit must open after failure_threshold consecutive failures"

    started = time.monotonic()
    with pytest.raises(PaymentServiceUnavailable):
        client.get_payment(str(uuid.uuid4()))
    elapsed = time.monotonic() - started
    assert elapsed < 0.05, "an open circuit must reject immediately, with no network attempt"


# --- payment-down end-to-end: booking stays PENDING ---

def test_confirm_fails_closed_when_payment_service_down(routing, booking_db):
    """Requires the payment service process to be stopped for the
    duration of this test (e.g. find its PID via `lsof -ti :8004` and
    kill it, then restart it the way scripts/dev.sh does afterward) --
    there is no API to do this in-process, since the point is a real
    downstream outage. Self-skips (rather than silently passing) if
    payment is actually reachable when this runs."""
    try:
        resp = httpx.get(f"{PAYMENT_DIRECT_BASE}/health", timeout=2.0)
        payment_reachable = resp.status_code == 200
    except httpx.TransportError:
        payment_reachable = False

    if payment_reachable:
        pytest.skip("payment service is currently reachable -- stop it before running this test")

    seats = [make_seat("A1", 0, 0)]
    showtime, _layout = make_showtime_with_seats(routing, seats, base_price=60.0)
    seat_ids = showtime_seat_ids_by_label(booking_db, showtime["id"])
    a1 = seat_ids["A1"]

    create_resp = create_booking(routing, showtime["id"], [a1])
    assert create_resp.status_code == 201, create_resp.text
    booking_id = create_resp.json()["id"]

    fake_payment_id = str(uuid.uuid4())
    confirm_resp = routing.post(f"/booking/bookings/{booking_id}/confirm", json={"payment_id": fake_payment_id})
    assert confirm_resp.status_code == 503, confirm_resp.text

    get_resp = routing.get(f"/booking/bookings/{booking_id}")
    assert get_resp.json()["status"] == "PENDING", "booking must stay PENDING, not be left in a broken state"

    with booking_db.cursor() as cur:
        cur.execute("SELECT status FROM showtime_seat WHERE id = %s", (a1,))
        assert cur.fetchone()["status"] == "LOCKED", "seat must remain LOCKED, not BOOKED, since confirm never reached the seat update"
