"""Phase 9.6 verification (AI agent requirements doc §2.1) -- the new
business logic at POST /booking/showtimes/{id}/find-seats. Real
Postgres, a real published seat layout with known positions, via the
live routing/theatre/booking processes -- same convention as every
other tests/integration/test_phase*.py file (no mocked DB).

Coordinate/adjacency conventions confirmed with the user before writing
this (see services/booking/application/seat_finder.py's docstring):
position_x/position_y are raw layout units, normalized per-showtime
before zone/centrality classification; "adjacent" requires the same row
(exact position_y) plus close position_x, not just the same zone.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

ROUTING_BASE = "http://localhost:8000"


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


def unique(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


def new_admin() -> str:
    return str(uuid.uuid4())


def make_screen(routing) -> tuple[str, str]:
    theatres = routing.get("/theatre/theatres").json()
    city_id = theatres[0]["city_id"]
    theatre_resp = routing.post("/theatre/admin/theatres", json={"city_id": city_id, "name": unique("P9.6 Theatre")})
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


def create_showtime(routing, screen_id: str, base_price: float = 100.0, movie_title: str = "P9.6 Movie") -> dict:
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


def grid_three_rows(cols: int = 10) -> list[dict]:
    """3 rows (A/B/C at y=0/1/2), 10 seats each at x=0..9. Min-max
    normalized over y in [0, 2]: row A -> norm_y 0.0 (front), row B ->
    norm_y 0.5 (middle), row C -> norm_y 1.0 (back) -- exactly one row
    per zone, so the "expected centre-middle pair" is unambiguous
    (no tie between two equally-central rows in the same zone)."""
    seats = []
    for row_index, row_letter in enumerate("ABC"):
        for col in range(cols):
            seats.append(make_seat(f"{row_letter}{col + 1}", x=float(col), y=float(row_index)))
    return seats


def make_showtime_with_seats(routing, seats: list[dict], base_price: float = 100.0) -> dict:
    _theatre_id, screen_id = make_screen(routing)
    publish_layout(routing, screen_id, seats)
    return create_showtime(routing, screen_id, base_price=base_price)


def find_seats(routing, showtime_id: str, count: int, **prefs) -> httpx.Response:
    return routing.post(
        f"/booking/showtimes/{showtime_id}/find-seats",
        json={"count": count, "preferences": prefs},
    )


def test_find_seats_top_result_is_centre_middle_pair(routing):
    showtime = make_showtime_with_seats(routing, grid_three_rows(), base_price=100.0)

    resp = find_seats(routing, showtime["id"], count=2, adjacent=True, zone="middle", seat_type="any")
    assert resp.status_code == 200, resp.text
    groups = resp.json()

    assert len(groups) >= 1, groups
    top = groups[0]
    assert top["zone"] == "middle", top
    labels = sorted(s["label"] for s in top["seats"])
    assert labels == ["B5", "B6"], labels
    assert top["description"] == "Row B, seats 5-6, centre, standard", top["description"]
    assert top["total_price"] == pytest.approx(200.0), top


def test_find_seats_respects_seat_type_preference(routing):
    seats = grid_three_rows()
    # Upgrade row B's centre seats to RECLINER so the seat_type filter
    # has something real to exclude/include.
    for seat in seats:
        if seat["label"] in ("B5", "B6"):
            seat["seat_type"] = "RECLINER"
            seat["price_multiplier"] = 1.5
    showtime = make_showtime_with_seats(routing, seats, base_price=100.0)

    resp = find_seats(routing, showtime["id"], count=2, adjacent=True, zone="middle", seat_type="standard")
    assert resp.status_code == 200, resp.text
    groups = resp.json()
    for group in groups:
        for seat in group["seats"]:
            assert seat["seat_type"] == "STANDARD", group

    recliner_resp = find_seats(routing, showtime["id"], count=2, adjacent=True, zone="middle", seat_type="recliner")
    assert recliner_resp.status_code == 200, recliner_resp.text
    recliner_groups = recliner_resp.json()
    assert len(recliner_groups) == 1, recliner_groups
    assert sorted(s["label"] for s in recliner_groups[0]["seats"]) == ["B5", "B6"]
    assert recliner_groups[0]["total_price"] == pytest.approx(300.0)


def test_find_seats_no_groups_when_count_exceeds_zone_capacity(routing):
    showtime = make_showtime_with_seats(routing, grid_three_rows(cols=3), base_price=100.0)

    resp = find_seats(routing, showtime["id"], count=10, adjacent=True, zone="middle", seat_type="any")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_find_seats_404_when_showtime_not_materialized(routing):
    resp = find_seats(routing, str(uuid.uuid4()), count=2)
    assert resp.status_code == 404, resp.text
