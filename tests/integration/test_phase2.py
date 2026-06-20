"""Phase 2 verification criteria (implementation-plan.md):

- Full layout lifecycle: create draft with a flat seat list -> edit
  individual and bulk-selected seats -> publish -> confirm ACTIVE and
  assigned to the screen in one transaction (§4.5).
- Draft lock contention: two simulated admin sessions, second blocked with
  409 + current holder info while the first holds the lock (§4.6).
- Staleness reclaim: acquire a lock, stop heartbeats, advance time past
  the ~2 minute threshold, confirm a second session can now acquire it.
- Lock-ownership-not-just-existence: acquire, let it go stale, attempt a
  save with the now-stale-holder's own credentials -- confirm rejection
  even though a lock record still technically exists.
- Clone: publish a layout, clone it to a second screen, confirm fresh
  UUIDs with identical labels/positions/types.

Runs against the real docker-compose stack plus every service started by
`scripts/dev.sh`, same as test_phase1.py. There is no admin/test endpoint
for moving the server's clock, and the design doc deliberately specifies a
read-time staleness check (no sweep worker) rather than a TTL the server
tracks itself -- so "mock the clock" here means directly backdating
`lock_heartbeat_at` in theatre_db, which is equivalent for a read-time
check and doesn't require any test-only hook in production code.

Caller identity for lock-gated endpoints: AUTH_ENABLED=false (today's
default) means there's no JWT-derived user yet (Phase 7), so these tests
identify each simulated admin session via the X-Admin-User-Id header
(see services/theatre/main.py's _get_admin_identity).
"""
import os
import uuid
from typing import Optional

import httpx
import psycopg2
import pytest

ROUTING_BASE = "http://localhost:8000"
THEATRE_DB_URL = os.environ.get(
    "THEATRE_DATABASE_URL",
    "postgresql://movieticket:movieticket_dev_password@localhost:5433/theatre_db",
)


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


@pytest.fixture
def theatre_db():
    conn = psycopg2.connect(THEATRE_DB_URL)
    try:
        yield conn
    finally:
        conn.close()


def unique(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


def admin_headers(admin_id: str) -> dict:
    return {"X-Admin-User-Id": admin_id}


def new_admin() -> str:
    return str(uuid.uuid4())


def make_screen(routing) -> tuple[str, str]:
    """Creates a fresh theatre+screen and returns (theatre_id, screen_id).
    No admin screen-listing endpoint exists yet, so each test builds its
    own rather than depending on shared seed data."""
    theatres = routing.get("/theatre/theatres").json()
    city_id = theatres[0]["city_id"]
    theatre_resp = routing.post(
        "/theatre/admin/theatres", json={"city_id": city_id, "name": unique("Phase2 Theatre")}
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


def create_draft(routing, screen_id: str, seats: list[dict], name: Optional[str] = None) -> dict:
    resp = routing.post(
        "/theatre/admin/seat-layouts/draft",
        json={"screen_id": screen_id, "name": name or unique("Layout"), "seats": seats},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def backdate_heartbeat(theatre_db, draft_id: str, minutes_ago: float) -> None:
    with theatre_db.cursor() as cur:
        cur.execute(
            "UPDATE seat_layout SET lock_heartbeat_at = now() - INTERVAL '1 minute' * %s WHERE id = %s",
            (minutes_ago, draft_id),
        )
    theatre_db.commit()


# --- full layout lifecycle ---

def test_full_layout_lifecycle_create_edit_publish(routing):
    _theatre_id, screen_id = make_screen(routing)
    seats = [
        make_seat("A1", 0, 0),
        make_seat("A2", 1, 0),
        make_seat("B1", 0, 1, seat_type="PREMIUM", price_multiplier=1.5),
    ]
    draft = create_draft(routing, screen_id, seats)
    draft_id = draft["id"]
    assert draft["status"] == "DRAFT"
    assert draft["screen_id"] == screen_id
    assert {s["label"] for s in draft["seats"]} == {"A1", "A2", "B1"}

    admin = new_admin()
    lock_resp = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin))
    assert lock_resp.status_code == 200, lock_resp.text
    assert lock_resp.json()["locked_by_user_id"] == admin

    a1_id = next(s["id"] for s in draft["seats"] if s["label"] == "A1")
    single_patch = routing.patch(
        f"/theatre/admin/seat-layouts/draft/{draft_id}/seats/{a1_id}",
        json={"price_multiplier": 2.0},
        headers=admin_headers(admin),
    )
    assert single_patch.status_code == 200, single_patch.text
    assert single_patch.json()["price_multiplier"] == 2.0

    a2_id = next(s["id"] for s in draft["seats"] if s["label"] == "A2")
    b1_id = next(s["id"] for s in draft["seats"] if s["label"] == "B1")
    bulk_patch = routing.patch(
        f"/theatre/admin/seat-layouts/draft/{draft_id}/seats",
        json={"seat_ids": [a2_id, b1_id], "seat_type": "RECLINER"},
        headers=admin_headers(admin),
    )
    assert bulk_patch.status_code == 200, bulk_patch.text
    assert {s["seat_type"] for s in bulk_patch.json()} == {"RECLINER"}

    publish_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft_id}/publish", headers=admin_headers(admin)
    )
    assert publish_resp.status_code == 200, publish_resp.text
    published = publish_resp.json()
    assert published["status"] == "ACTIVE"
    assert published["screen_id"] == screen_id
    assert published["locked_by_user_id"] is None
    seats_by_label = {s["label"]: s for s in published["seats"]}
    assert seats_by_label["A1"]["price_multiplier"] == 2.0
    assert seats_by_label["A2"]["seat_type"] == "RECLINER"
    assert seats_by_label["B1"]["seat_type"] == "RECLINER"


def test_patch_without_lock_is_rejected(routing):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    seat_id = draft["seats"][0]["id"]

    resp = routing.patch(
        f"/theatre/admin/seat-layouts/draft/{draft['id']}/seats/{seat_id}",
        json={"label": "A1-renamed"},
        headers=admin_headers(new_admin()),
    )
    assert resp.status_code == 403, resp.text


# --- draft lock contention ---

def test_lock_contention_second_session_blocked_with_holder_info(routing):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    draft_id = draft["id"]

    admin_a = new_admin()
    admin_b = new_admin()

    first = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_a))
    assert first.status_code == 200, first.text

    second = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_b))
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["locked_by_user_id"] == admin_a


def test_lock_heartbeat_refresh_by_same_holder_succeeds(routing):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    draft_id = draft["id"]
    admin = new_admin()

    first = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin))
    assert first.status_code == 200
    acquired_at = first.json()["lock_acquired_at"]

    second = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin))
    assert second.status_code == 200, second.text
    assert second.json()["locked_by_user_id"] == admin
    assert second.json()["lock_acquired_at"] == acquired_at, "heartbeat refresh must not reset acquired_at"


def test_explicit_release_then_others_can_acquire(routing):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    draft_id = draft["id"]
    admin_a, admin_b = new_admin(), new_admin()

    routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_a))

    blocked = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_b))
    assert blocked.status_code == 409

    release = routing.delete(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_a))
    assert release.status_code == 204, release.text

    now_free = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_b))
    assert now_free.status_code == 200, now_free.text
    assert now_free.json()["locked_by_user_id"] == admin_b


def test_release_by_non_holder_is_rejected(routing):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    draft_id = draft["id"]
    admin_a, admin_b = new_admin(), new_admin()

    routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_a))
    resp = routing.delete(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_b))
    assert resp.status_code == 409, resp.text


# --- staleness reclaim ---

def test_staleness_reclaim_after_threshold(routing, theatre_db):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    draft_id = draft["id"]
    admin_a, admin_b = new_admin(), new_admin()

    routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_a))

    still_blocked = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_b))
    assert still_blocked.status_code == 409, "fresh heartbeat must not be reclaimable yet"

    backdate_heartbeat(theatre_db, draft_id, minutes_ago=3)

    reclaimed = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin_b))
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["locked_by_user_id"] == admin_b


# --- lock-ownership, not merely existence ---

def test_stale_holders_own_save_is_rejected_despite_lock_record_existing(routing, theatre_db):
    _theatre_id, screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])
    draft_id = draft["id"]
    seat_id = draft["seats"][0]["id"]
    admin = new_admin()

    lock_resp = routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin))
    assert lock_resp.status_code == 200

    backdate_heartbeat(theatre_db, draft_id, minutes_ago=3)

    with theatre_db.cursor() as cur:
        cur.execute("SELECT locked_by_user_id FROM seat_layout WHERE id = %s", (draft_id,))
        still_recorded = cur.fetchone()[0]
    assert still_recorded == admin, "lock record must still technically name the original holder"

    save_attempt = routing.patch(
        f"/theatre/admin/seat-layouts/draft/{draft_id}/seats/{seat_id}",
        json={"label": "A1-renamed"},
        headers=admin_headers(admin),
    )
    assert save_attempt.status_code == 403, save_attempt.text

    publish_attempt = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft_id}/publish", headers=admin_headers(admin)
    )
    assert publish_attempt.status_code == 403, publish_attempt.text


# --- clone ---

def test_clone_publishes_to_second_screen_with_fresh_ids(routing):
    _theatre_id, screen_id = make_screen(routing)
    _target_theatre_id, target_screen_id = make_screen(routing)

    seats = [make_seat("A1", 0, 0, seat_type="PREMIUM", price_multiplier=1.5), make_seat("A2", 1, 0)]
    draft = create_draft(routing, screen_id, seats)
    draft_id = draft["id"]
    admin = new_admin()

    routing.post(f"/theatre/admin/seat-layouts/draft/{draft_id}/lock", headers=admin_headers(admin))
    publish_resp = routing.post(
        f"/theatre/admin/seat-layouts/draft/{draft_id}/publish", headers=admin_headers(admin)
    )
    assert publish_resp.status_code == 200, publish_resp.text

    clone_resp = routing.post(
        f"/theatre/admin/seat-layouts/{draft_id}/clone", json={"target_screen_id": target_screen_id}
    )
    assert clone_resp.status_code == 201, clone_resp.text
    cloned = clone_resp.json()
    assert cloned["screen_id"] == target_screen_id
    assert cloned["id"] != draft_id
    assert cloned["status"] == "DRAFT"

    original_by_label = {s["label"]: s for s in seats}
    cloned_ids = {s["id"] for s in cloned["seats"]}
    original_ids = {s["id"] for s in seats}
    assert cloned_ids.isdisjoint(original_ids), "clone must mint fresh UUIDs per seat"
    assert len(cloned["seats"]) == len(seats)
    for seat in cloned["seats"]:
        source = original_by_label[seat["label"]]
        assert seat["position_x"] == source["x"]
        assert seat["position_y"] == source["y"]
        assert seat["seat_type"] == source["seat_type"]
        assert seat["price_multiplier"] == source["price_multiplier"]


def test_clone_rejects_a_layout_that_is_still_draft(routing):
    _theatre_id, screen_id = make_screen(routing)
    _target_theatre_id, target_screen_id = make_screen(routing)
    draft = create_draft(routing, screen_id, [make_seat("A1", 0, 0)])

    resp = routing.post(
        f"/theatre/admin/seat-layouts/{draft['id']}/clone", json={"target_screen_id": target_screen_id}
    )
    assert resp.status_code == 409, resp.text


# --- exit criteria: a realistic 150+ seat layout, fully freeform ---

def test_admin_can_author_a_realistic_150_seat_layout(routing):
    _theatre_id, screen_id = make_screen(routing)
    seats = []
    # "line" tool conceptually: a straight run of seats at constant y.
    for col in range(20):
        seats.append(make_seat(f"A{col + 1}", x=col, y=0))
    # "grid" tool conceptually: rows x columns block.
    for row in range(1, 8):
        for col in range(20):
            seats.append(make_seat(f"{chr(65 + row)}{col + 1}", x=col, y=row, seat_type="PREMIUM", price_multiplier=1.25))
    # "single-seat" tool conceptually: a few ad hoc wheelchair spots, not
    # aligned to any row/column.
    seats.append(make_seat("W1", x=2.5, y=7.5, seat_type="ACCESSIBLE", price_multiplier=1.0))
    seats.append(make_seat("W2", x=17.5, y=7.5, seat_type="ACCESSIBLE", price_multiplier=1.0))

    assert len(seats) >= 150

    draft = create_draft(routing, screen_id, seats)
    assert len(draft["seats"]) == len(seats)
    assert {s["label"] for s in draft["seats"]} == {s["label"] for s in seats}
