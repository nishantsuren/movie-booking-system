"""Phase 1 verification criteria (implementation-plan.md):

- Full CRUD lifecycle for movies/releases/theatres/screens via admin endpoints.
- Customer browse endpoints return correctly filtered/city-scoped results.
- Asset upload (admin) then retrieval (public) round-trips correctly.
- Soft-delete: a deactivated movie disappears from browse but still
  resolves by ID, non-cascading (§4.2).
- Idempotency for create endpoints is server-derived from identity-defining
  fields (design v9, §11.1) -- no client-supplied Idempotency-Key header.

Runs against the real docker-compose stack (Postgres/Redis) plus every
service started by `scripts/dev.sh` -- nothing here is mocked. The
routing service fronts catalog/theatre; the local CDN mock is hit
directly, per design doc §3 ("Both apps load... directly from the local
CDN mock").

Test data uses a random suffix in titles/names: since the create
endpoints now derive their dedup key from those exact fields (§11.1),
reusing a literal fixed title/name across separate test runs against a
persistent dev database would collide with a previous run's (possibly
already-renamed-or-deleted) row.
"""
import uuid

import httpx
import pytest

ROUTING_BASE = "http://localhost:8000"
CDN_BASE = "http://localhost:8006"


@pytest.fixture
def routing():
    with httpx.Client(base_url=ROUTING_BASE, timeout=10.0) as client:
        yield client


@pytest.fixture
def cdn():
    with httpx.Client(base_url=CDN_BASE, timeout=10.0) as client:
        yield client


def unique(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:8]}"


# --- full CRUD lifecycle: movies, releases, theatres, screens ---

def test_movie_crud_lifecycle(routing):
    title = unique("Test Movie CRUD")
    create_resp = routing.post(
        "/catalog/admin/movies",
        json={"title": title, "description": "desc", "duration_minutes": 100, "language": "English"},
    )
    assert create_resp.status_code == 201, create_resp.text
    movie = create_resp.json()
    assert movie["title"] == title
    assert movie["is_active"] is True
    movie_id = movie["id"]

    update_resp = routing.put(f"/catalog/admin/movies/{movie_id}", json={"title": f"{title} (updated)"})
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["title"] == f"{title} (updated)"

    get_resp = routing.get(f"/catalog/movies/{movie_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["title"] == f"{title} (updated)"

    delete_resp = routing.delete(f"/catalog/admin/movies/{movie_id}")
    assert delete_resp.status_code == 204

    after_delete = routing.get(f"/catalog/movies/{movie_id}")
    assert after_delete.status_code == 200
    assert after_delete.json()["is_active"] is False


def test_movie_release_crud_lifecycle(routing):
    movie_resp = routing.post(
        "/catalog/admin/movies",
        json={"title": unique("Release Lifecycle Movie"), "duration_minutes": 110},
    )
    movie_id = movie_resp.json()["id"]
    city_id = str(uuid.uuid4())  # loose reference -- no FK to theatre service

    create_resp = routing.post(
        f"/catalog/admin/movies/{movie_id}/releases",
        json={"city_id": city_id, "release_date": "2026-06-01", "planned_end_date": "2026-09-01"},
    )
    assert create_resp.status_code == 201, create_resp.text
    release = create_resp.json()
    assert release["city_id"] == city_id
    release_id = release["id"]

    update_resp = routing.put(f"/catalog/admin/releases/{release_id}", json={"actual_end_date": "2026-08-15"})
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["actual_end_date"] == "2026-08-15"


def test_theatre_and_screen_crud_lifecycle(routing):
    # Theatre admin endpoints have a real FK to city within theatre_db,
    # so create a city-backed row first. No admin endpoint creates cities
    # at this phase, so go through the seed-style direct route: reuse an
    # already-seeded city by looking it up via the city-scoped browse.
    theatres = routing.get("/theatre/theatres").json()
    assert theatres, "expected seed data to be present"
    city_id = theatres[0]["city_id"]

    theatre_name = unique("Test Theatre CRUD")
    create_resp = routing.post(
        "/theatre/admin/theatres",
        json={"city_id": city_id, "name": theatre_name, "address": "123 Test St"},
    )
    assert create_resp.status_code == 201, create_resp.text
    theatre = create_resp.json()
    theatre_id = theatre["id"]

    update_resp = routing.put(f"/theatre/admin/theatres/{theatre_id}", json={"address": "456 Updated Ave"})
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["address"] == "456 Updated Ave"

    screen_resp = routing.post(
        f"/theatre/admin/theatres/{theatre_id}/screens",
        json={"name": "Screen 1"},
    )
    assert screen_resp.status_code == 201, screen_resp.text
    screen = screen_resp.json()
    assert screen["theatre_id"] == theatre_id
    screen_id = screen["id"]

    screen_update = routing.put(f"/theatre/admin/screens/{screen_id}", json={"name": "Screen 1 (IMAX)"})
    assert screen_update.status_code == 200, screen_update.text
    assert screen_update.json()["name"] == "Screen 1 (IMAX)"

    get_theatre = routing.get(f"/theatre/theatres/{theatre_id}")
    assert get_theatre.status_code == 200
    assert get_theatre.json()["name"] == theatre_name


# --- city-scoped customer browse ---

def test_movies_browse_is_city_scoped(routing):
    theatres = routing.get("/theatre/theatres").json()
    city_ids = {t["city_id"] for t in theatres}
    assert len(city_ids) >= 2, "expected seed data across multiple cities"

    results_per_city = {}
    for city_id in city_ids:
        resp = routing.get("/catalog/movies", params={"city": city_id})
        assert resp.status_code == 200
        results_per_city[city_id] = {m["title"] for m in resp.json()}

    # at least one city's result set differs from another's -- proves the
    # filter is actually scoping, not just returning everything regardless
    assert len(set(map(frozenset, results_per_city.values()))) > 1


def test_theatres_browse_is_city_scoped(routing):
    all_theatres = routing.get("/theatre/theatres").json()
    city_ids = {t["city_id"] for t in all_theatres}
    one_city = next(iter(city_ids))

    scoped = routing.get("/theatre/theatres", params={"city": one_city}).json()
    assert scoped, "expected at least one theatre in this seeded city"
    assert all(t["city_id"] == one_city for t in scoped)
    assert len(scoped) <= len(all_theatres)


# --- asset upload/retrieve round-trip ---

def test_asset_upload_and_retrieve_round_trip(cdn):
    file_content = unique("fake-poster-bytes-for-testing").encode()
    upload_resp = cdn.post("/assets", files={"file": ("poster.jpg", file_content, "image/jpeg")})
    assert upload_resp.status_code == 201, upload_resp.text
    asset = upload_resp.json()
    asset_id = asset["id"]
    assert asset["filename"] == "poster.jpg"
    assert asset["byte_size"] == len(file_content)

    get_resp = cdn.get(f"/assets/{asset_id}")
    assert get_resp.status_code == 200
    assert get_resp.content == file_content
    assert get_resp.headers["content-type"] == "image/jpeg"


def test_asset_upload_retry_with_identical_bytes_does_not_duplicate(cdn):
    """No client-supplied key at all: the server derives the dedup key from
    a hash of the uploaded bytes (§11.1), so resending identical content --
    e.g. a client retrying after a dropped response -- always lands on the
    same asset row."""
    file_content = unique("retry-test-bytes").encode()

    first = cdn.post("/assets", files={"file": ("retry.jpg", file_content, "image/jpeg")})
    second = cdn.post("/assets", files={"file": ("retry.jpg", file_content, "image/jpeg")})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"], "replayed upload must return the original asset"


# --- idempotency is server-derived, no client key, for catalog/theatre too ---

def test_create_movie_with_identical_data_is_deduplicated_without_a_client_key(routing):
    title = unique("Dedup Movie")
    body = {"title": title, "duration_minutes": 100, "language": "English"}

    first = routing.post("/catalog/admin/movies", json=body)
    second = routing.post("/catalog/admin/movies", json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"], "identical create payload must dedupe without any header"


def test_create_theatre_with_identical_data_is_deduplicated_without_a_client_key(routing):
    theatres = routing.get("/theatre/theatres").json()
    city_id = theatres[0]["city_id"]
    body = {"city_id": city_id, "name": unique("Dedup Theatre"), "address": "1 Dedup Way"}

    first = routing.post("/theatre/admin/theatres", json=body)
    second = routing.post("/theatre/admin/theatres", json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"], "identical create payload must dedupe without any header"


# --- soft-delete: non-cascading, browse-visibility-only ---

def test_soft_delete_non_cascading(routing):
    movie_resp = routing.post(
        "/catalog/admin/movies",
        json={"title": unique("Soft Delete Target"), "duration_minutes": 90},
    )
    movie_id = movie_resp.json()["id"]
    city_id = str(uuid.uuid4())

    release_resp = routing.post(
        f"/catalog/admin/movies/{movie_id}/releases",
        json={"city_id": city_id, "release_date": "2026-01-01"},
    )
    assert release_resp.status_code == 201
    release_id = release_resp.json()["id"]

    before = routing.get("/catalog/movies", params={"city": city_id}).json()
    assert any(m["id"] == movie_id for m in before), "movie should be visible in browse before delete"

    delete_resp = routing.delete(f"/catalog/admin/movies/{movie_id}")
    assert delete_resp.status_code == 204

    after = routing.get("/catalog/movies", params={"city": city_id}).json()
    assert all(m["id"] != movie_id for m in after), "deactivated movie must disappear from browse"

    direct = routing.get(f"/catalog/movies/{movie_id}")
    assert direct.status_code == 200, "soft-deleted movie must still resolve by ID (§4.2)"
    assert direct.json()["is_active"] is False

    release_check = routing.put(f"/catalog/admin/releases/{release_id}", json={})
    assert release_check.status_code == 200, "release row must survive the movie's soft-delete, non-cascading"
    assert release_check.json()["id"] == release_id
