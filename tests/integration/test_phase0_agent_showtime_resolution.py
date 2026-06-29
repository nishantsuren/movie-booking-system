"""COLLECTING_SHOWTIME state verification -- real routing/catalog/
theatre/agent processes, no mocks, same convention as every other
tests/integration/test_phase*.py file.

Unlike test_phase0_agent_theatre_selection.py, this file creates its
own movie+theatre+screen+showtime fixtures via the admin API rather
than reading pre-seeded data: real seed data only ever has one
showtime per theatre+movie (confirmed while building this state), so
the multi-showtime paths this state exists for (date narrowing, "which
time" with several real options, a button click picking a specific
one) have no real data to test against otherwise.
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


def _publish_layout(routing, screen_id: str) -> None:
    seats = [{"id": str(uuid.uuid4()), "label": "A1", "x": 0, "y": 0, "seat_type": "STANDARD", "price_multiplier": 1.0}]
    draft = routing.post(
        "/theatre/admin/seat-layouts/draft", json={"screen_id": screen_id, "name": "Layout", "seats": seats}
    ).json()
    admin = str(uuid.uuid4())
    routing.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/lock", headers={"X-Admin-User-Id": admin})
    routing.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/publish", headers={"X-Admin-User-Id": admin})


def _create_movie_with_showtimes(routing, city: dict, start_times: list[datetime]) -> tuple[dict, dict, list[dict]]:
    """A fresh movie with an active release in `city`, a fresh
    theatre+screen+published seat layout, and one activated showtime
    per entry in start_times -- fully self-contained, independent of
    whatever else is already seeded."""
    movie = routing.post(
        "/catalog/admin/movies", json={"title": unique("Test Showtime Movie"), "duration_minutes": 120}
    ).json()
    routing.post(
        f"/catalog/admin/movies/{movie['id']}/releases",
        json={"city_id": city["id"], "release_date": "2026-01-01", "planned_end_date": "2026-12-31"},
    )
    theatre = routing.post(
        "/theatre/admin/theatres", json={"city_id": city["id"], "name": unique("Test Showtime Theatre")}
    ).json()
    screen = routing.post(f"/theatre/admin/theatres/{theatre['id']}/screens", json={"name": "Screen 1"}).json()
    _publish_layout(routing, screen["id"])

    showtimes = []
    for start_time in start_times:
        showtime = routing.post(
            "/theatre/admin/showtimes",
            json={
                "movie_id": movie["id"],
                "movie_title": movie["title"],
                "screen_id": screen["id"],
                "start_time": start_time.isoformat(),
                "base_price": 200.0,
            },
        ).json()
        routing.post(f"/theatre/admin/showtimes/{showtime['id']}/activate")
        showtimes.append(showtime)

    return movie, theatre, showtimes


def _advance_to_theatre(routing, session_id: str, city: dict, movie: dict, theatre: dict, date_index: int = 0):
    # selected_option throughout -- deterministic setup, bypassing
    # nlu.py entirely, same reasoning as test_phase0_agent_theatre_
    # selection.py's _advance_to_showtime.
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": city["name"], "selected_option": city["name"]},
    )
    movie_resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": movie["title"], "selected_option": movie["title"]},
    )
    # CollectingDateState now sits between movie and theatre -- even a
    # single real date requires an explicit click before the
    # conversation can move on to naming a theatre (see
    # dialogue_manager.py's CollectingDateState). date_index lets a
    # caller pick a specific date when a fixture has more than one
    # (test_date_narrows_multiple_showtimes_to_one).
    date_option = movie_resp.json()["options"][date_index]
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": date_option, "selected_option": date_option},
    )
    return routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": theatre["name"], "selected_option": theatre["name"]},
    )


def test_single_showtime_presented_as_option_then_resolves_on_click(routing):
    city = routing.get("/theatre/cities").json()[0]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, _ = _create_movie_with_showtimes(routing, city, [start])

    session_id = str(uuid.uuid4())
    resp = _advance_to_theatre(routing, session_id, city, movie, theatre)
    body = resp.json()

    # Even a single real showtime must be presented as a one-item
    # options list requiring an explicit click, not auto-resolved --
    # this is exactly the bug CollectingShowtimeState's old
    # len(candidates) == 1 auto-resolve shortcut had (see
    # dialogue_manager.py).
    assert body["state"] == "COLLECTING_SHOWTIME"
    assert len(body["options"]) == 1

    chosen = body["options"][0]
    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": chosen, "selected_option": chosen},
    )
    body2 = resp2.json()

    # Clicking the only real option now actually resolves it --
    # cascading the same turn into AWAITING_BOOKING (showtime is the
    # last slot-filling state; nothing stops the walk there).
    assert body2["state"] == "AWAITING_BOOKING"
    assert body2["options"] == []


def test_multiple_showtimes_lists_them_as_options_and_asks(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, _ = _create_movie_with_showtimes(routing, city, [base, base + timedelta(hours=4)])

    resp = _advance_to_theatre(routing, str(uuid.uuid4()), city, movie, theatre)
    body = resp.json()

    assert body["state"] == "COLLECTING_SHOWTIME"
    assert len(body["options"]) == 2


def test_date_narrows_multiple_showtimes_to_one(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    second_day = base + timedelta(days=1)
    movie, theatre, _ = _create_movie_with_showtimes(routing, city, [base, second_day])

    # Two distinct calendar dates -- CollectingDateState now resolves
    # the date *before* theatre/showtime ever run, so date-narrowing
    # happens by clicking the second day's date option there, not by
    # mentioning a date late as free text at the showtime stage.
    # _unique_date_labels sorts chronologically, so date_index=1 is
    # the later (second) day.
    day_name = second_day.strftime("%A")
    session_id = str(uuid.uuid4())
    resp = _advance_to_theatre(routing, session_id, city, movie, theatre, date_index=1)
    body = resp.json()

    # CollectingShowtimeState's own date-narrowing (unchanged) still
    # narrows showtimes-at-the-theatre down to the one on the chosen
    # day -- just fed a context.date that was already confirmed one
    # step earlier. Still a one-item options list, not auto-resolved
    # (same single-value rule as everywhere else).
    assert body["state"] == "COLLECTING_SHOWTIME"
    assert len(body["options"]) == 1
    assert day_name in body["response"]


def test_clicking_a_showtime_option_resolves_that_specific_one(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, _ = _create_movie_with_showtimes(routing, city, [base, base + timedelta(hours=4)])

    session_id = str(uuid.uuid4())
    resp = _advance_to_theatre(routing, session_id, city, movie, theatre)
    options = resp.json()["options"]
    assert len(options) == 2

    chosen = options[1]
    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": chosen, "selected_option": chosen},
    )
    body = resp2.json()

    # Resolving this showtime cascades the same turn into
    # AWAITING_BOOKING (showtime is the last slot-filling state).
    assert body["state"] == "AWAITING_BOOKING"
    assert body["options"] == []
    assert body["extra"]["entities"]["showtime"] == chosen
