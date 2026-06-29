"""COLLECTING_MOVIE state verification -- real routing/catalog/theatre/
agent processes, no mocks, same convention as every other
tests/integration/test_phase*.py file. Movie/city names are read from
the live seed data rather than hardcoded, since seed data can change.
"""
import uuid

import httpx
import pytest

ROUTING_BASE = "http://localhost:8000"


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


def _seeded_city_with_movies(routing):
    """A real city plus the real movies currently playing there --
    skips the test rather than guessing if seed data doesn't support it."""
    for city in routing.get("/theatre/cities").json():
        movies = routing.get("/catalog/movies", params={"city": city["id"]}).json()
        if movies:
            return city, movies
    pytest.skip("no seeded city has any currently-playing movie to test against")


def test_naming_city_and_real_movie_in_one_message_resolves_both(routing):
    city, movies = _seeded_city_with_movies(routing)
    movie = movies[0]

    resp = routing.post(
        "/agent/message",
        json={
            "session_id": str(uuid.uuid4()),
            "message": f"{movie['title']} in {city['name']}",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Orchestrator always walks the full priority list every turn (see
    # dialogue_manager.py) -- once city+movie both resolve in this one
    # message, it continues straight into COLLECTING_DATE the same
    # turn rather than stopping at COLLECTING_MOVIE. It stops there,
    # not at COLLECTING_THEATRE, because CollectingDateState always
    # requires an explicit match/click before resolving (even a single
    # real date), and this message named no date at all.
    #
    # Case-insensitive: responder.articulate() runs at nonzero
    # temperature (by design, for phrasing variety) and occasionally
    # re-cases a proper noun ("Monsoon Drift" -> "monsoon drift")
    # while rephrasing -- not a dialogue_manager.py bug, its own state
    # already holds the exact-cased real title internally.
    assert body["state"] == "COLLECTING_DATE"
    assert movie["title"].lower() in body["response"].lower()


def test_setting_only_city_prompts_for_a_real_movie(routing):
    city, movies = _seeded_city_with_movies(routing)
    session_id = str(uuid.uuid4())

    resp = routing.post("/agent/message", json={"session_id": session_id, "message": city["name"]})
    body = resp.json()

    assert body["state"] == "COLLECTING_MOVIE"
    assert any(movie["title"] in body["response"] for movie in movies)


def test_unmatched_movie_shows_real_currently_playing_list(routing):
    city, movies = _seeded_city_with_movies(routing)
    session_id = str(uuid.uuid4())

    routing.post("/agent/message", json={"session_id": session_id, "message": city["name"]})
    resp = routing.post(
        "/agent/message",
        json={"session_id": session_id, "message": "Some Made Up Movie That Does Not Exist"},
    )
    body = resp.json()

    assert body["state"] == "COLLECTING_MOVIE"
    assert any(movie["title"] in body["response"] for movie in movies)


def test_correcting_city_after_picking_a_movie_re_prompts_for_the_new_citys_movies(routing):
    cities = routing.get("/theatre/cities").json()
    by_city = [(city, routing.get("/catalog/movies", params={"city": city["id"]}).json()) for city in cities]

    # Need two cities whose currently-playing movie lists differ, so a
    # movie picked for the first is provably not assumed valid for the
    # second -- skip if seed data doesn't give us that.
    first_city = first_movies = second_city = second_movies = None
    for city_a, movies_a in by_city:
        if not movies_a:
            continue
        for city_b, movies_b in by_city:
            if city_b["id"] != city_a["id"] and movies_b and movies_a[0]["title"] not in {m["title"] for m in movies_b}:
                first_city, first_movies, second_city, second_movies = city_a, movies_a, city_b, movies_b
                break
        if first_city:
            break
    if not first_city:
        pytest.skip("no two seeded cities have provably different currently-playing movies")

    session_id = str(uuid.uuid4())
    routing.post("/agent/message", json={"session_id": session_id, "message": first_city["name"]})
    routing.post("/agent/message", json={"session_id": session_id, "message": first_movies[0]["title"]})

    resp = routing.post("/agent/message", json={"session_id": session_id, "message": f"actually {second_city['name']}"})
    body = resp.json()

    assert body["state"] == "COLLECTING_MOVIE"
    assert first_movies[0]["title"] not in body["response"]
    assert any(movie["title"] in body["response"] for movie in second_movies)
