"""COLLECTING_DATE state verification -- real routing/catalog/theatre/
agent processes, no mocks, same convention as every other
tests/integration/test_phase*.py file.

Unlike test_phase0_agent_theatre_selection.py, this file creates its
own movie+theatre+screen+showtime fixtures via the admin API rather
than reading pre-seeded data: real seed data only ever has one
calendar date per movie+city (every theatre's offset stays within the
same day -- confirmed while building this state), so the multi-date
paths this state exists for (which date, narrowing by typed text, a
button click picking a specific one, correcting an already-resolved
date) have no real data to test against otherwise. Same self-contained-
fixture convention as test_phase0_agent_showtime_resolution.py.
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
        "/catalog/admin/movies", json={"title": unique("Test Date Movie"), "duration_minutes": 120}
    ).json()
    routing.post(
        f"/catalog/admin/movies/{movie['id']}/releases",
        json={"city_id": city["id"], "release_date": "2026-01-01", "planned_end_date": "2026-12-31"},
    )
    theatre = routing.post(
        "/theatre/admin/theatres", json={"city_id": city["id"], "name": unique("Test Date Theatre")}
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


def _advance_to_date(routing, session_id: str, city: dict, movie: dict):
    """City + movie clicks only -- stops right at CollectingDateState,
    returning that turn's response so a test can inspect/click its
    date options directly."""
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": city["name"], "selected_option": city["name"]},
    )
    return routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": movie["title"], "selected_option": movie["title"]},
    )


def test_single_date_presented_as_option_then_resolves_on_click(routing):
    city = routing.get("/theatre/cities").json()[0]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    movie, _, _ = _create_movie_with_showtimes(routing, city, [start])

    session_id = str(uuid.uuid4())
    resp = _advance_to_date(routing, session_id, city, movie)
    body = resp.json()

    # Even a single real date must be presented as a one-item options
    # list requiring an explicit click, not auto-written -- the same
    # rule the showtime bug fix applies (see dialogue_manager.py's
    # CollectingDateState/CollectingShowtimeState).
    assert body["state"] == "COLLECTING_DATE"
    assert len(body["options"]) == 1

    chosen = body["options"][0]
    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": chosen, "selected_option": chosen},
    )
    body2 = resp2.json()

    # Clicking the only real date resolves it and moves on to theatre.
    assert body2["state"] == "COLLECTING_THEATRE"


def test_multiple_dates_lists_them_as_options_and_asks(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    movie, _, _ = _create_movie_with_showtimes(routing, city, [base, base + timedelta(days=1)])

    session_id = str(uuid.uuid4())
    resp = _advance_to_date(routing, session_id, city, movie)
    body = resp.json()

    assert body["state"] == "COLLECTING_DATE"
    assert len(body["options"]) == 2


def test_typed_date_text_resolves_the_matching_date(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    second_day = base + timedelta(days=1)
    movie, _, _ = _create_movie_with_showtimes(routing, city, [base, second_day])

    session_id = str(uuid.uuid4())
    _advance_to_date(routing, session_id, city, movie)

    # A natural sentence, not the bare weekday name -- llama3.2:3b is
    # inconsistent at extracting a date field from a bare word with no
    # surrounding context (documented fragility, see
    # agent_service_progress.md; verified live: "Tuesday" alone
    # returns nothing, "the Tuesday show please" extracts it fine).
    day_name = second_day.strftime("%A")
    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": f"the {day_name} show please"},
    )
    body = resp.json()

    # Resolves the date and cascades straight into asking for theatre
    # (none chosen yet) the same turn.
    assert body["state"] == "COLLECTING_THEATRE"


def test_clicking_a_date_option_resolves_that_specific_one(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    movie, _, _ = _create_movie_with_showtimes(routing, city, [base, base + timedelta(days=1)])

    session_id = str(uuid.uuid4())
    resp = _advance_to_date(routing, session_id, city, movie)
    options = resp.json()["options"]
    assert len(options) == 2

    chosen = options[1]
    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": chosen, "selected_option": chosen},
    )
    body = resp2.json()

    assert body["state"] == "COLLECTING_THEATRE"
    assert body["extra"]["entities"]["date"] == chosen


def test_unmatched_date_text_rejects_with_real_list(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    movie, _, _ = _create_movie_with_showtimes(routing, city, [base, base + timedelta(days=1)])

    session_id = str(uuid.uuid4())
    resp = _advance_to_date(routing, session_id, city, movie)
    real_options = resp.json()["options"]

    # selected_option bypasses nlu.py entirely (deterministic) -- used
    # here only to exercise CollectingDateState's reject-with-list
    # branch for an unmatched value without depending on llama3.2:3b's
    # extraction reliability for an unusual date phrase. A real button
    # click never sends a value the agent didn't itself offer, but
    # nothing stops a test from doing so to hit this branch directly.
    fake_date = "Some Made Up Date That Does Not Exist"
    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": fake_date, "selected_option": fake_date},
    )
    body = resp2.json()

    # Asserting on body["options"], not body["response"] text --
    # responder.articulate()'s nonzero temperature can mangle a
    # synthetic, hex-suffixed test fixture name while rephrasing
    # (documented elsewhere in this codebase); options bypasses
    # articulation entirely, so it's the reliable signal here.
    assert body["state"] == "COLLECTING_DATE"
    assert body["options"] == real_options


def test_correcting_date_after_resolving_clears_theatre_and_showtime(routing):
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    second_day = base + timedelta(days=1)
    movie, theatre, _ = _create_movie_with_showtimes(routing, city, [base, second_day])

    session_id = str(uuid.uuid4())
    resp = _advance_to_date(routing, session_id, city, movie)
    first_date_option = resp.json()["options"][0]

    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": first_date_option, "selected_option": first_date_option},
    )
    theatre_resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": theatre["name"], "selected_option": theatre["name"]},
    )
    # Fully resolved against the first date: the one real showtime is
    # presented, click it to actually write context.showtime_id.
    showtime_option = theatre_resp.json()["options"][0]
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": showtime_option, "selected_option": showtime_option},
    )

    day_name = second_day.strftime("%A")
    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": f"the {day_name} show please"},
    )
    body = resp2.json()

    # A real change to context.date (Orchestrator.process()'s new
    # correction-clearing block) must clear the stale theatre/showtime
    # picked against the old date -- re-asking for theatre proves both
    # got cleared, even though this fixture's one theatre would have
    # worked for the new date too. Asserting on body["options"], not
    # body["response"] text -- responder.articulate() can mangle this
    # synthetic, hex-suffixed fixture name while rephrasing.
    assert body["state"] == "COLLECTING_THEATRE"
    assert theatre["name"] in body["options"]
