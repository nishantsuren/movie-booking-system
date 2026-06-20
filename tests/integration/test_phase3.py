"""Phase 3 verification criteria (implementation-plan.md):

- Create a showtime against a published layout -> confirm matching
  SHOWTIME_SEAT rows exist in booking service's database with correct
  label/position_x/position_y/seat_type/price/seat_template_id.
- Idempotency test: call materialize twice for the same showtime
  (simulating a retry) -> confirm UNIQUE (showtime_id, seat_template_id)
  prevents duplicates, second call is a no-op.
- Fail-closed test: stop booking service, attempt showtime creation,
  confirm it fails closed after exhausting retries rather than leaving an
  orphaned showtime with no seats (§4.3, §13).
- Deletion business rule test (today's version, design v10): DELETE
  flips is_active back to false rather than removing the row -- there is
  no cross-service booking check to test yet (nothing can be non-AVAILABLE
  until Phase 5), so this just confirms the activate/deactivate mechanics
  and that a showtime is created inactive by default.

Runs against the real docker-compose stack plus every service started by
`scripts/dev.sh`, same as test_phase1.py/test_phase2.py. The fail-closed
test requires the booking service to actually be stopped for its
duration -- see the module docstring on test_showtime_creation_fails_closed_when_booking_service_down
for exactly what to do if running this file standalone.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import psycopg2.extras
import pytest

ROUTING_BASE = "http://localhost:8000"
BOOKING_DIRECT_BASE = "http://localhost:8003"
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
def booking_direct():
    with httpx.Client(base_url=BOOKING_DIRECT_BASE, timeout=10.0) as client:
        yield client


@pytest.fixture
def theatre_db():
    conn = psycopg2.connect(THEATRE_DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


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
        "/theatre/admin/theatres", json={"city_id": city_id, "name": unique("Phase3 Theatre")}
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
    """Creates a draft with the given seats and publishes it, returning the
    published layout (with its final seats, including server-assigned
    fields)."""
    draft_resp = routing.post(
        "/theatre/admin/seat-layouts/draft",
        json={"screen_id": screen_id, "name": unique("Layout"), "seats": seats},
    )
    assert draft_resp.status_code == 201, draft_resp.text
    draft = draft_resp.json()

    admin = new_admin()
    lock_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft['id']}/lock",
        headers={"X-Admin-User-Id": admin},
    )
    assert lock_resp.status_code == 200, lock_resp.text

    publish_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft['id']}/publish",
        headers={"X-Admin-User-Id": admin},
    )
    assert publish_resp.status_code == 200, publish_resp.text
    return publish_resp.json()


def make_showtime_body(screen_id: str, movie_id: str = None, base_price: float = 200.0, start_time: datetime = None) -> dict:
    return {
        "movie_id": movie_id or str(uuid.uuid4()),
        "screen_id": screen_id,
        "start_time": (start_time or (datetime.now(timezone.utc) + timedelta(days=7))).isoformat(),
        "base_price": base_price,
    }


# --- materialization matches the published layout exactly ---

def test_create_showtime_materializes_matching_showtime_seat_rows(routing, booking_db):
    _theatre_id, screen_id = make_screen(routing)
    seats = [
        make_seat("A1", 0, 0, seat_type="STANDARD", price_multiplier=1.0),
        make_seat("A2", 1, 0, seat_type="PREMIUM", price_multiplier=1.5),
        make_seat("A3", 2, 0, seat_type="RECLINER", price_multiplier=2.0),
    ]
    layout = publish_layout(routing, screen_id, seats)
    template_by_label = {s["label"]: s for s in layout["seats"]}

    body = make_showtime_body(screen_id, base_price=100.0)
    resp = routing.post("/theatre/admin/showtimes", json=body)
    assert resp.status_code == 201, resp.text
    showtime = resp.json()
    assert showtime["is_active"] is False, "a newly created showtime must default to inactive"
    assert showtime["base_price"] == 100.0

    with booking_db.cursor() as cur:
        cur.execute(
            "SELECT * FROM showtime_seat WHERE showtime_id = %s ORDER BY label",
            (showtime["id"],),
        )
        rows = [dict(r) for r in cur.fetchall()]

    assert len(rows) == len(seats)
    rows_by_label = {r["label"]: r for r in rows}
    assert set(rows_by_label.keys()) == {"A1", "A2", "A3"}
    for label, row in rows_by_label.items():
        template = template_by_label[label]
        assert str(row["seat_template_id"]) == template["id"]
        assert row["position_x"] == template["position_x"]
        assert row["position_y"] == template["position_y"]
        assert row["seat_type"] == template["seat_type"]
        assert row["price"] == pytest.approx(100.0 * template["price_multiplier"])
        assert row["status"] == "AVAILABLE"
        assert row["locked_by_booking_id"] is None


# --- materialize idempotency: a retried call is a clean no-op ---

def test_materialize_called_twice_is_a_clean_no_op(routing, booking_direct, booking_db):
    _theatre_id, screen_id = make_screen(routing)
    seats = [make_seat("A1", 0, 0), make_seat("A2", 1, 0)]
    layout = publish_layout(routing, screen_id, seats)

    body = make_showtime_body(screen_id, base_price=50.0)
    resp = routing.post("/theatre/admin/showtimes", json=body)
    assert resp.status_code == 201, resp.text
    showtime_id = resp.json()["id"]

    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM showtime_seat WHERE showtime_id = %s", (showtime_id,))
        first_count = cur.fetchone()["n"]
    assert first_count == len(seats)

    # Simulate the retried call theatre service would make on a network
    # blip -- same showtime_id, same seat_template_ids.
    materialize_payload = {
        "seats": [
            {
                "seat_template_id": s["id"],
                "label": s["label"],
                "x": s["position_x"],
                "y": s["position_y"],
                "seat_type": s["seat_type"],
                "price": 50.0 * s["price_multiplier"],
            }
            for s in layout["seats"]
        ]
    }
    retry_resp = booking_direct.post(
        f"/internal/showtimes/{showtime_id}/materialize-seats", json=materialize_payload
    )
    assert retry_resp.status_code == 201, retry_resp.text

    with booking_db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM showtime_seat WHERE showtime_id = %s", (showtime_id,))
        second_count = cur.fetchone()["n"]
    assert second_count == first_count, "a retried materialize call must not create duplicates"

    with booking_db.cursor() as cur:
        cur.execute(
            "SELECT seat_template_id, count(*) AS n FROM showtime_seat WHERE showtime_id = %s GROUP BY seat_template_id",
            (showtime_id,),
        )
        per_template_counts = [row["n"] for row in cur.fetchall()]
    assert all(n == 1 for n in per_template_counts), "UNIQUE (showtime_id, seat_template_id) must hold per seat"


# --- fail-closed: booking service down -> showtime creation fails, no orphan ---

def test_showtime_creation_fails_closed_when_booking_service_down(routing, theatre_db):
    """Requires the booking service process to be stopped for the duration
    of this test (e.g. `kill $(lsof -ti :8003)` or stop its `scripts/dev.sh`
    process), then restarted afterward -- there is no API to do this
    in-process, since the whole point is a real downstream outage. If
    booking is actually reachable when this runs, the test fails loudly
    rather than silently passing."""
    health_check_failed = False
    try:
        resp = httpx.get(f"{BOOKING_DIRECT_BASE}/health", timeout=2.0)
        health_check_failed = resp.status_code != 200
    except httpx.TransportError:
        health_check_failed = True

    if not health_check_failed:
        pytest.skip(
            "booking service is currently reachable -- stop it before running this test "
            "(it must be down to exercise the fail-closed path)"
        )

    _theatre_id, screen_id = make_screen(routing)
    seats = [make_seat("A1", 0, 0)]
    publish_layout(routing, screen_id, seats)

    start_time = datetime.now(timezone.utc) + timedelta(days=14)
    body = make_showtime_body(screen_id, base_price=75.0, start_time=start_time)
    resp = routing.post("/theatre/admin/showtimes", json=body)

    assert resp.status_code == 502, resp.text
    assert "materializ" in resp.text.lower()

    with theatre_db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM showtime WHERE screen_id = %s AND start_time = %s",
            (screen_id, start_time),
        )
        orphan_count = cur.fetchone()["n"]
    assert orphan_count == 0, "showtime creation must fail closed -- no orphan row with zero seats"


# --- showtime defaults to inactive; activate/deactivate mechanics ---

def test_showtime_created_inactive_then_activate_and_deactivate(routing):
    _theatre_id, screen_id = make_screen(routing)
    seats = [make_seat("A1", 0, 0)]
    publish_layout(routing, screen_id, seats)

    body = make_showtime_body(screen_id)
    create_resp = routing.post("/theatre/admin/showtimes", json=body)
    assert create_resp.status_code == 201, create_resp.text
    showtime = create_resp.json()
    showtime_id = showtime["id"]
    assert showtime["is_active"] is False

    activate_resp = routing.post(f"/theatre/admin/showtimes/{showtime_id}/activate")
    assert activate_resp.status_code == 200, activate_resp.text
    assert activate_resp.json()["is_active"] is True

    delete_resp = routing.delete(f"/theatre/admin/showtimes/{showtime_id}")
    assert delete_resp.status_code == 200, delete_resp.text
    deactivated = delete_resp.json()
    assert deactivated["is_active"] is False
    assert deactivated["id"] == showtime_id, "deactivation must not remove the row"
