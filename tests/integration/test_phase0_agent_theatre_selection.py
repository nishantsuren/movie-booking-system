"""COLLECTING_THEATRE state + resolution.py verification -- real
routing/catalog/theatre/agent processes, no mocks, same convention as
every other tests/integration/test_phase*.py file. Theatre/movie/city
names are read from the live seed data rather than hardcoded, since
seed data can change.

This is the test file agent_service_progress.md's "Next session" asked
for once the deterministic difflib resolution layer landed -- it
re-verifies the exact live conversations that surfaced the original
NLU misrouting bug ("How about Orion mall", "INOX Mantri Square") and
adds standard COLLECTING_THEATRE coverage (unmatched theatre,
correction to a different theatre, theatre-derives-city pre-step).
"""
import re
import uuid

import httpx
import pytest

ROUTING_BASE = "http://localhost:8000"

# This dev DB accumulates fixture rows from every other phase's test
# runs (movies/theatres named e.g. "Dedup Movie 2516250d", "P9.5
# Theatre 005620ba") alongside the handful of real-looking seeded
# names ("Monsoon Drift", "PVR Orion Mall"). llama3.2:3b reliably fails
# to extract anything at all from the fixture-style names (hex/numeric
# suffixes, no resemblance to a real proper noun) -- that's a sparse-
# training-data gap in the model, not something resolution.py's
# similarity layer can or should compensate for (there's no real
# candidate for it to correct *to*). Filtering them out of the
# candidate pool here keeps these tests about the disambiguation bug
# this file exists to cover, not about that separate gap.
_LOOKS_LIKE_TEST_FIXTURE = re.compile(r"\d")


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


def _theatre_names(showtimes):
    seen = []
    for st in showtimes:
        if st["theatre_name"] not in seen and not _LOOKS_LIKE_TEST_FIXTURE.search(st["theatre_name"]):
            seen.append(st["theatre_name"])
    return seen


def _seeded_city_movie_with_multiple_theatres(routing):
    """A real city + currently-playing movie that's screening at two or
    more distinct theatres there -- needed to exercise "which theatre"
    disambiguation rather than auto-resolving on a single option."""
    for city in routing.get("/theatre/cities").json():
        for movie in routing.get("/catalog/movies", params={"city": city["id"]}).json():
            if _LOOKS_LIKE_TEST_FIXTURE.search(movie["title"]):
                continue
            result = routing.get(f"/theatre/movies/{movie['id']}/showtimes", params={"city": city["id"]}).json()
            names = _theatre_names(result["showtimes"])
            if len(names) >= 2:
                return city, movie, names
    pytest.skip("no seeded city has a real-looking currently-playing movie screening at 2+ real-looking theatres")


def _seeded_city_movie_with_single_theatre(routing):
    """A real city + currently-playing movie screening at exactly one
    theatre there -- the simplest case, no theatre name needed at all
    to auto-resolve once asked."""
    for city in routing.get("/theatre/cities").json():
        for movie in routing.get("/catalog/movies", params={"city": city["id"]}).json():
            if _LOOKS_LIKE_TEST_FIXTURE.search(movie["title"]):
                continue
            result = routing.get(f"/theatre/movies/{movie['id']}/showtimes", params={"city": city["id"]}).json()
            names = _theatre_names(result["showtimes"])
            if len(names) == 1:
                return city, movie, names[0]
    pytest.skip("no seeded city has a real-looking currently-playing movie screening at exactly 1 real-looking theatre")


def _theatre_named(routing, name):
    for theatre in routing.get("/theatre/theatres").json():
        if theatre["name"].lower() == name.lower():
            return theatre
    return None


def _advance_to_showtime(routing, session_id, city, movie):
    # Deterministic setup via selected_option (a button click, bypassing
    # nlu.py entirely) -- not a stand-in for testing free text. llama3.2:3b
    # is inconsistent at extracting a movie field from a bare proper noun
    # with zero surrounding context (e.g. "Monsoon Drift" alone returns
    # nothing at all); these turns only exist to get to the
    # COLLECTING_THEATRE state this file actually tests, so they use the
    # one path guaranteed not to depend on that.
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
    # dialogue_manager.py's CollectingDateState; real seed data only
    # ever has one date per movie+city, but this clicks whichever
    # option comes first regardless of how many there are).
    date_option = movie_resp.json()["options"][0]
    routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": date_option, "selected_option": date_option},
    )


def test_naming_real_theatre_resolves_showtime(routing):
    city, movie, theatre_name = _seeded_city_movie_with_single_theatre(routing)
    session_id = str(uuid.uuid4())
    _advance_to_showtime(routing, session_id, city, movie)

    resp = routing.post("/agent/message", json={"session_id": session_id, "message": theatre_name})
    body = resp.json()

    # Orchestrator always walks the full priority list every turn --
    # resolving theatre here cascades straight into COLLECTING_SHOWTIME
    # the same turn, which presents the one real showtime at it as a
    # one-item options list rather than silently resolving it (even a
    # single real candidate now requires an explicit click -- see
    # dialogue_manager.py's CollectingShowtimeState).
    assert body["state"] == "COLLECTING_SHOWTIME"
    assert len(body["options"]) == 1
    assert theatre_name in body["response"]
    assert "Which theatre" not in body["response"]


def test_unmatched_theatre_shows_real_theatre_list(routing):
    city, movie, theatre_names = _seeded_city_movie_with_multiple_theatres(routing)
    session_id = str(uuid.uuid4())
    _advance_to_showtime(routing, session_id, city, movie)

    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "Some Made Up Theatre That Does Not Exist"},
    )
    body = resp.json()

    assert body["state"] == "COLLECTING_THEATRE"
    assert any(name in body["response"] for name in theatre_names)


def test_theatre_named_with_no_city_or_movie_set_derives_city(routing):
    """Orchestrator._resolve_city_from_theatre() pre-step: a theatre
    named with no city said at all should derive that theatre's home
    city and move straight to COLLECTING_MOVIE for it, not get stuck
    asking which city."""
    theatre = _theatre_named(routing, "INOX Mantri Square")
    if theatre is None:
        pytest.skip("seed data has no theatre named 'INOX Mantri Square' to test against")
    city = next(c for c in routing.get("/theatre/cities").json() if c["id"] == theatre["city_id"])
    movies = routing.get("/catalog/movies", params={"city": city["id"]}).json()
    if not movies:
        pytest.skip("INOX Mantri Square's home city has no currently-playing movie to prompt for")

    resp = routing.post(
        "/agent/message",
        json={"session_id": str(uuid.uuid4()), "message": "INOX Mantri Square"},
    )
    body = resp.json()

    assert body["state"] == "COLLECTING_MOVIE"
    assert any(movie["title"] in body["response"] for movie in movies)


def test_known_misrouted_theatre_message_resolves_correctly(routing):
    """Regression test for the exact live-tested repro in
    agent_service_progress.md: "INOX Mantri Square" used to get split
    by nlu.py across city="Mantri Square"/movie="INOX"/
    theatre="INOX Mantri Square" in one message, and GreetingState
    rejected "Mantri Square" as an unsupported city before
    CollectingTheatreState ever got a turn. resolution.py's difflib
    layer re-categorizes every fragment to "theatre" and clears the
    other two slots, so this should resolve straight through instead."""
    theatre = _theatre_named(routing, "INOX Mantri Square")
    if theatre is None:
        pytest.skip("seed data has no theatre named 'INOX Mantri Square' to test against")
    city = next(c for c in routing.get("/theatre/cities").json() if c["id"] == theatre["city_id"])
    movies = routing.get("/catalog/movies", params={"city": city["id"]}).json()
    showing_here = [
        m for m in movies
        if not _LOOKS_LIKE_TEST_FIXTURE.search(m["title"])
        and "INOX Mantri Square" in _theatre_names(
            routing.get(f"/theatre/movies/{m['id']}/showtimes", params={"city": city["id"]}).json()["showtimes"]
        )
    ]
    if not showing_here:
        pytest.skip("INOX Mantri Square isn't currently screening any real-looking seeded movie")
    movie = showing_here[0]

    session_id = str(uuid.uuid4())
    _advance_to_showtime(routing, session_id, city, movie)

    resp = routing.post("/agent/message", json={"session_id": session_id, "message": "INOX Mantri Square"})
    body = resp.json()

    # Case-insensitive: responder.articulate() runs at nonzero
    # temperature (by design, for phrasing variety) and occasionally
    # re-cases a proper noun ("INOX" -> "Inox") while rephrasing --
    # not a resolution.py bug, dialogue_manager's own state already
    # holds the exact-cased real name internally. State is
    # COLLECTING_SHOWTIME, not COLLECTING_THEATRE, because resolving
    # theatre here cascades straight into COLLECTING_SHOWTIME the same
    # turn (it presents the one real showtime as an option rather than
    # silently resolving it -- see test_naming_real_theatre_resolves_
    # showtime above).
    assert body["state"] == "COLLECTING_SHOWTIME"
    assert "inox mantri square" in body["response"].lower()
    assert "don't support" not in body["response"]
    assert "couldn't find that movie" not in body["response"]


def test_fuzzy_theatre_name_normalizes_to_real_name(routing):
    """Regression test for the other live-tested repro: a casual,
    differently-capitalized theatre mention ("Orion mall") should
    normalize to the platform's real name ("PVR Orion Mall") via
    resolution.py's difflib match, not get rejected as unmatched."""
    theatre = _theatre_named(routing, "PVR Orion Mall")
    if theatre is None:
        pytest.skip("seed data has no theatre named 'PVR Orion Mall' to test against")
    city = next(c for c in routing.get("/theatre/cities").json() if c["id"] == theatre["city_id"])
    movies = routing.get("/catalog/movies", params={"city": city["id"]}).json()
    showing_here = [
        m for m in movies
        if not _LOOKS_LIKE_TEST_FIXTURE.search(m["title"])
        and "PVR Orion Mall" in _theatre_names(
            routing.get(f"/theatre/movies/{m['id']}/showtimes", params={"city": city["id"]}).json()["showtimes"]
        )
    ]
    if not showing_here:
        pytest.skip("PVR Orion Mall isn't currently screening any seeded movie")
    movie = showing_here[0]

    session_id = str(uuid.uuid4())
    _advance_to_showtime(routing, session_id, city, movie)

    # Capitalized "How about" specifically -- llama3.2:3b is sensitive
    # to casing here in a non-obvious way (verified live: lowercase
    # "how about orion mall" returns nothing at all, even though the
    # few-shot example it's modeled on is itself lowercase). A known
    # model quirk, not something to chase per nlu.py's own documented
    # lesson on this -- use the phrasing actually verified to work.
    resp = routing.post("/agent/message", json={"session_id": session_id, "message": "How about Orion mall"})
    body = resp.json()

    # COLLECTING_SHOWTIME, not COLLECTING_THEATRE -- resolving theatre
    # here cascades straight into COLLECTING_SHOWTIME the same turn,
    # presenting the one real showtime as an option (see
    # test_naming_real_theatre_resolves_showtime above).
    assert body["state"] == "COLLECTING_SHOWTIME"
    assert "PVR Orion Mall" in body["response"]


def test_correcting_theatre_resolves_the_new_one(routing):
    city, movie, theatre_names = _seeded_city_movie_with_multiple_theatres(routing)
    session_id = str(uuid.uuid4())
    _advance_to_showtime(routing, session_id, city, movie)

    routing.post("/agent/message", json={"session_id": session_id, "message": theatre_names[0]})
    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": f"switch to {theatre_names[1]}"},
    )
    body = resp.json()

    # Case-insensitive for the same reason as the misrouting regression
    # test above -- responder.articulate()'s nonzero temperature can
    # re-case a proper noun while rephrasing. State is COLLECTING_SHOWTIME
    # because resolving the corrected theatre cascades into presenting its
    # one real showtime as an option, the same turn (see
    # test_naming_real_theatre_resolves_showtime above). The theatre-
    # change clearing context.showtime_id (Orchestrator.process()) is
    # what guarantees the option offered is the *new* theatre's showtime,
    # not a stale one carried over from the old theatre.
    assert body["state"] == "COLLECTING_SHOWTIME"
    assert theatre_names[1].lower() in body["response"].lower()
    assert theatre_names[0].lower() not in body["response"].lower()
