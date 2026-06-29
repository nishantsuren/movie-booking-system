"""AWAITING_BOOKING state verification -- real routing/catalog/theatre/
booking/payment/agent processes, no mocks, same convention as every
other tests/integration/test_phase*.py file.

Self-contained fixtures via the admin API (same convention as
test_phase0_agent_showtime_resolution.py/test_phase0_agent_date_selection.py)
-- real seed data's movies/theatres are already heavily booked-against
by other test runs, and this file specifically needs full control over
booking lifecycle (PENDING/CONFIRMED/CANCELLED), not just showtime
shape.
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


def _create_theatre_with_showtime(routing, city: dict, movie_id: str, movie_title: str, start_time: datetime) -> tuple[dict, dict]:
    theatre = routing.post(
        "/theatre/admin/theatres", json={"city_id": city["id"], "name": unique("Test Awaiting Theatre")}
    ).json()
    screen = routing.post(f"/theatre/admin/theatres/{theatre['id']}/screens", json={"name": "Screen 1"}).json()
    _publish_layout(routing, screen["id"])
    showtime = routing.post(
        "/theatre/admin/showtimes",
        json={
            "movie_id": movie_id,
            "movie_title": movie_title,
            "screen_id": screen["id"],
            "start_time": start_time.isoformat(),
            "base_price": 200.0,
        },
    ).json()
    routing.post(f"/theatre/admin/showtimes/{showtime['id']}/activate")
    return theatre, showtime


def _create_movie_with_showtime(routing, city: dict, start_time: datetime) -> tuple[dict, dict, dict]:
    """A fresh movie with an active release in `city` plus one fresh
    theatre+screen+published seat layout+activated showtime --
    self-contained, independent of whatever else is already seeded."""
    movie = routing.post(
        "/catalog/admin/movies", json={"title": unique("Test Awaiting Movie"), "duration_minutes": 120}
    ).json()
    routing.post(
        f"/catalog/admin/movies/{movie['id']}/releases",
        json={"city_id": city["id"], "release_date": "2026-01-01", "planned_end_date": "2026-12-31"},
    )
    theatre, showtime = _create_theatre_with_showtime(routing, city, movie["id"], movie["title"], start_time)
    return movie, theatre, showtime


def _create_movie_with_multiple_showtimes(routing, city: dict, start_times: list[datetime]) -> tuple[dict, dict, list[dict]]:
    """Same fresh-movie setup as _create_movie_with_showtime, but one
    theatre with 2+ showtimes on the *same* calendar date -- needed to
    reproduce a real bug: once a showtime is resolved among several
    same-day candidates, every later turn must be a silent no-op, not
    re-ask "which one" forever (the original len(candidates) == 1
    no-op check only covered the single-candidate case)."""
    movie = routing.post(
        "/catalog/admin/movies", json={"title": unique("Test Awaiting Movie"), "duration_minutes": 120}
    ).json()
    routing.post(
        f"/catalog/admin/movies/{movie['id']}/releases",
        json={"city_id": city["id"], "release_date": "2026-01-01", "planned_end_date": "2026-12-31"},
    )
    theatre = routing.post(
        "/theatre/admin/theatres", json={"city_id": city["id"], "name": unique("Test Awaiting Theatre")}
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


def _create_movie_with_two_dates(routing, city: dict, day_a: datetime, day_b: datetime) -> tuple[dict, dict, dict, dict]:
    """Same fresh-movie setup as _create_movie_with_showtime, but one
    theatre with two showtimes on two distinct calendar dates -- needed
    to exercise a date correction deterministically. Uses a date
    correction (not a theatre correction) specifically to avoid typed
    free text ever needing to extract a synthetic, hex-suffixed
    theatre name -- a documented llama3.2:3b weak spot elsewhere in
    this codebase (see test_phase0_agent_theatre_selection.py's
    _LOOKS_LIKE_TEST_FIXTURE) -- dates are real ISO timestamps and
    extract reliably via the same phrasing other agent test files
    already verified."""
    movie = routing.post(
        "/catalog/admin/movies", json={"title": unique("Test Awaiting Movie"), "duration_minutes": 120}
    ).json()
    routing.post(
        f"/catalog/admin/movies/{movie['id']}/releases",
        json={"city_id": city["id"], "release_date": "2026-01-01", "planned_end_date": "2026-12-31"},
    )
    theatre = routing.post(
        "/theatre/admin/theatres", json={"city_id": city["id"], "name": unique("Test Awaiting Theatre")}
    ).json()
    screen = routing.post(f"/theatre/admin/theatres/{theatre['id']}/screens", json={"name": "Screen 1"}).json()
    _publish_layout(routing, screen["id"])

    showtimes = []
    for day in (day_a, day_b):
        showtime = routing.post(
            "/theatre/admin/showtimes",
            json={
                "movie_id": movie["id"],
                "movie_title": movie["title"],
                "screen_id": screen["id"],
                "start_time": day.isoformat(),
                "base_price": 200.0,
            },
        ).json()
        routing.post(f"/theatre/admin/showtimes/{showtime['id']}/activate")
        showtimes.append(showtime)

    return movie, theatre, showtimes[0], showtimes[1]


def _create_booking(routing, showtime_id: str) -> dict:
    seatmap = routing.get(f"/booking/showtimes/{showtime_id}/seatmap").json()
    seat_id = next(s["id"] for s in seatmap["seats"] if s["status"] == "AVAILABLE")
    resp = routing.post(
        "/booking/bookings",
        json={"showtime_id": showtime_id, "seat_ids": [seat_id], "user_id": str(uuid.uuid4())},
    )
    resp.raise_for_status()
    return resp.json()


def _confirm_booking(routing, booking: dict) -> dict:
    payment = routing.post(
        "/payment/payments", json={"booking_id": booking["id"], "amount": booking["price_paid"]}
    ).json()
    resp = routing.post(f"/booking/bookings/{booking['id']}/confirm", json={"payment_id": payment["id"]})
    resp.raise_for_status()
    return resp.json()


def _advance_to_awaiting_booking(routing, session_id: str, city: dict, movie: dict, theatre: dict):
    # selected_option throughout -- deterministic setup, bypassing
    # nlu.py entirely, same reasoning as every other agent test file's
    # advance-the-conversation helper.
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": city["name"], "selected_option": city["name"]},
    )
    movie_resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": movie["title"], "selected_option": movie["title"]},
    )
    date_option = movie_resp.json()["options"][0]
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": date_option, "selected_option": date_option},
    )
    theatre_resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": theatre["name"], "selected_option": theatre["name"]},
    )
    showtime_option = theatre_resp.json()["options"][0]
    return routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": showtime_option, "selected_option": showtime_option},
    )


def test_awaiting_booking_offers_seatmap_link_once_showtime_resolved(routing):
    city = routing.get("/theatre/cities").json()[0]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, showtime = _create_movie_with_showtime(routing, city, start)

    session_id = str(uuid.uuid4())
    resp = _advance_to_awaiting_booking(routing, session_id, city, movie, theatre)
    body = resp.json()

    assert body["state"] == "AWAITING_BOOKING"
    assert body["options"] == []
    url = body["extra"].get("seat_selection_url")
    assert url is not None
    assert showtime["id"] in url
    assert session_id in url
    assert "checkout_url" not in body["extra"]


def test_posting_pending_booking_id_returns_checkout_link(routing):
    city = routing.get("/theatre/cities").json()[0]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, showtime = _create_movie_with_showtime(routing, city, start)

    session_id = str(uuid.uuid4())
    _advance_to_awaiting_booking(routing, session_id, city, movie, theatre)
    booking = _create_booking(routing, showtime["id"])

    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "", "booking_id": booking["id"]},
    )
    body = resp.json()

    assert body["state"] == "AWAITING_BOOKING"
    url = body["extra"].get("checkout_url")
    assert url is not None
    assert booking["id"] in url
    assert session_id in url
    assert "seat_selection_url" not in body["extra"]


def test_posting_confirmed_booking_id_concludes(routing):
    city = routing.get("/theatre/cities").json()[0]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, showtime = _create_movie_with_showtime(routing, city, start)

    session_id = str(uuid.uuid4())
    _advance_to_awaiting_booking(routing, session_id, city, movie, theatre)
    booking = _create_booking(routing, showtime["id"])
    confirmed = _confirm_booking(routing, booking)
    assert confirmed["status"] == "CONFIRMED"

    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "", "booking_id": booking["id"]},
    )
    body = resp.json()

    assert body["state"] == "AWAITING_BOOKING"
    assert "seat_selection_url" not in body["extra"]
    assert "checkout_url" not in body["extra"]


def test_cancelled_booking_clears_and_reoffers_seatmap(routing):
    city = routing.get("/theatre/cities").json()[0]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, showtime = _create_movie_with_showtime(routing, city, start)

    session_id = str(uuid.uuid4())
    _advance_to_awaiting_booking(routing, session_id, city, movie, theatre)
    booking = _create_booking(routing, showtime["id"])
    routing.delete(f"/booking/bookings/{booking['id']}")

    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "", "booking_id": booking["id"]},
    )
    body = resp.json()

    assert body["state"] == "AWAITING_BOOKING"
    url = body["extra"].get("seat_selection_url")
    assert url is not None
    assert showtime["id"] in url
    assert "checkout_url" not in body["extra"]

    # context.booking_id was actually cleared, not just this turn's
    # message text -- a later plain turn (no booking_id) must keep
    # offering the seatmap link, not silently fall back to a stale
    # cancelled booking.
    resp2 = routing.post("/agent/message", json={"session_id": session_id, "message": "hi"})
    body2 = resp2.json()
    assert body2["state"] == "AWAITING_BOOKING"
    assert body2["extra"].get("seat_selection_url") is not None


def test_date_correction_clears_booking_id(routing):
    city = routing.get("/theatre/cities").json()[0]
    day_a = datetime.now(timezone.utc) + timedelta(days=3)
    day_b = day_a + timedelta(days=1)
    movie, theatre, showtime_a, _ = _create_movie_with_two_dates(routing, city, day_a, day_b)

    session_id = str(uuid.uuid4())
    _advance_to_awaiting_booking(routing, session_id, city, movie, theatre)
    booking = _create_booking(routing, showtime_a["id"])
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "", "booking_id": booking["id"]},
    )

    # Correct to the other real date -- a natural sentence, not the
    # bare weekday name (documented llama3.2:3b fragility, see
    # test_phase0_agent_date_selection.py), the same verified-working
    # phrasing other agent test files already use.
    day_name = day_b.strftime("%A")
    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": f"the {day_name} show please"},
    )
    body = resp.json()

    # Date corrected; theatre needs re-confirming (the one real theatre
    # here still requires an explicit click, same single-value rule as
    # everywhere else) -- this turn's walk stops at COLLECTING_THEATRE,
    # never reaching AWAITING_BOOKING at all, yet context.booking_id
    # must already be cleared at this point (Orchestrator.process()'s
    # COLLECTING_DATE correction block), not just once some later turn
    # happens to reach COLLECTING_SHOWTIME's own block.
    assert body["state"] == "COLLECTING_THEATRE"
    assert len(body["options"]) == 1
    theatre_option = body["options"][0]

    theatre_resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": theatre_option, "selected_option": theatre_option},
    )
    showtime_option = theatre_resp.json()["options"][0]

    final_resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": showtime_option, "selected_option": showtime_option},
    )
    body3 = final_resp.json()

    # Fresh seatmap link, not theatre A's stale checkout_url -- proves
    # context.booking_id was actually cleared by the date correction,
    # not silently carried over to the new date/showtime.
    assert body3["state"] == "AWAITING_BOOKING"
    assert body3["extra"].get("seat_selection_url") is not None
    assert "checkout_url" not in body3["extra"]


def test_resolved_showtime_among_multiple_stays_silent_on_later_turns(routing):
    """Regression test for a real bug: CollectingShowtimeState's old
    silent-no-op check only fired when len(candidates) == 1. Once a
    real movie+theatre has 2+ showtimes the same day (true for some
    seed data now, see infra/seed/seed_manual_testing.py), every turn
    after the first resolution kept re-asking "which one" forever --
    which also meant the booking hand-off's resume turn could never
    reach AWAITING_BOOKING, since this state kept reporting
    resolved=False. Reproduced here with 2 same-day showtimes, clicking
    one, then sending an unrelated later turn (the booking_id resume
    signal) and asserting it actually reaches AWAITING_BOOKING's
    CONFIRMED branch instead of bouncing back to "which one"."""
    city = routing.get("/theatre/cities").json()[0]
    base = datetime.now(timezone.utc) + timedelta(days=3)
    movie, theatre, showtimes = _create_movie_with_multiple_showtimes(routing, city, [base, base + timedelta(hours=4)])

    # _advance_to_awaiting_booking already clicks through one of the
    # showtime options as its last step -- with 2 candidates, that
    # click resolving straight into AWAITING_BOOKING the same turn was
    # never the broken path (the explicit-click branch always worked
    # regardless of candidate count). The bug is specifically about
    # the *next* turn re-querying this already-resolved state.
    session_id = str(uuid.uuid4())
    resp = _advance_to_awaiting_booking(routing, session_id, city, movie, theatre)
    body = resp.json()
    assert body["state"] == "AWAITING_BOOKING"
    chosen_label = body["extra"]["entities"]["showtime"]
    resolved_showtime = next(st for st in showtimes if chosen_label.lower().endswith(_label_suffix(st)))

    booking = _create_booking(routing, resolved_showtime["id"])
    confirmed = _confirm_booking(routing, booking)
    assert confirmed["status"] == "CONFIRMED"

    resp2 = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "", "booking_id": booking["id"]},
    )
    body2 = resp2.json()

    assert body2["state"] == "AWAITING_BOOKING"
    assert "seat_selection_url" not in body2["extra"]
    assert "checkout_url" not in body2["extra"]


def _label_suffix(showtime: dict) -> str:
    # CollectingShowtimeState formats labels as "%A, %b %-d, %-I:%M %p"
    # -- the time portion (e.g. "2:00 pm") is the only part that
    # distinguishes same-day showtimes, used here to match a clicked
    # option's label back to the real showtime it came from.
    return datetime.fromisoformat(showtime["start_time"]).strftime("%-I:%M %p").lower()
