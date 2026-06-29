"""Manual-testing scenario seed -- a richer, deliberately varied dataset
for exercising the admin and customer web apps by hand, distinct from
seed.py's minimal Phase 1 baseline.

Creates: Bengaluru (if not already present, same deterministic-UUID
upsert technique as seed.py) as the primary scenario -- three theatres
in it, two screens per theatre (six screens total) each with a
genuinely different seat layout (different dimensions, a mix of
STANDARD/PREMIUM/RECLINER zones, two of the six using a curved back
row), four movies ("The Last Frame", "Monsoon Drift", "Glass Horizon",
"Dilli Dhadkan"), and every one of them running in every Bengaluru
theatre for ALL_MOVIE_RUN_DAYS calendar dates at SHOWS_PER_DAY shows
per date -- two movies sharing each screen (interleaved, non-colliding
start times), so manual testing (and the agent's COLLECTING_DATE/
COLLECTING_SHOWTIME multi-candidate paths) has real multi-date,
multiple-shows-per-day, multi-theatre data to exercise everywhere, not
just a single hand-picked theatre. Plus a second, lighter-weight city
(Mumbai, one theatre/screen/layout, a single showtime for just "The
Last Frame") -- not part of the user's original ask, but without it
this script alone left the database in a single-city state that fails
the automated regression suite's test_movies_browse_is_city_scoped
(tests/integration/test_phase1.py), which asserts theatres span >= 2
cities and that GET /catalog/movies?city= actually scopes results
differently per city. Mumbai deliberately keeps only one movie/showtime
(not the full all-movies/all-theatres treatment Bengaluru gets) -- that
asymmetry is what makes the "differs per city" assertion hold, not just
the city count.

Unlike seed.py, this goes through the real admin HTTP API (via the
routing service) rather than writing directly to each database -- a
seat layout draft and a showtime both have real multi-step server-side
behavior (draft lock/publish, booking service's seat materialization)
that a raw INSERT would bypass and leave inconsistent. Only CITY is
written directly, since it has no admin endpoint at this design phase
(§4.1) -- same as seed.py.

Requires the dev stack running (`scripts/dev.sh`). Idempotent: theatre/
screen/movie/release creation all dedupe via the server-derived
idempotency key (§11.1); re-running this without cleaning first creates
a second seat-layout draft and showtime per screen rather than erroring
-- pair with clean_db.py for a guaranteed-fresh run:

    python infra/seed/clean_db.py --reseed
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg2
import psycopg2.extras

ROUTING_BASE = os.environ.get("ROUTING_BASE", "http://localhost:8000")
PG_USER = os.getenv("POSTGRES_USER", "movieticket")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "movieticket_dev_password")
PG_HOST_PORT = os.getenv("POSTGRES_HOST_PORT", "5433")
PG_BASE = f"postgresql://{PG_USER}:{PG_PASSWORD}@localhost:{PG_HOST_PORT}"

# Same fixed namespace as seed.py -- not shared between the two scripts
# on purpose (they seed independent, non-overlapping data), but reusing
# the technique keeps city_id stable across repeated --reseed runs.
SEED_NAMESPACE = uuid.UUID("9f9e9c1a-0000-4000-8000-000000000002")

ADMIN_ID = str(uuid.uuid5(SEED_NAMESPACE, "manual-testing-admin"))
ADMIN_HEADERS = {"X-Admin-User-Id": ADMIN_ID}

# Every Bengaluru movie runs in every Bengaluru theatre for this many
# calendar dates, at this many shows/date -- real, multi-date,
# multiple-shows-per-day seed data rather than the single-showtime-per-
# movie+theatre shape this script used to have.
ALL_MOVIE_RUN_DAYS = 10
SHOWS_PER_DAY = 3

# 6 distinct start hours per screen per day -- two movies share a
# screen (see screen1_movies/screen2_movies in main()), each taking
# every other hour so neither movie ever collides with the other's
# start time.
SCREEN_HOURS = (9, 11, 13, 16, 18, 21)


def seed_id(slug: str) -> str:
    return str(uuid.uuid5(SEED_NAMESPACE, slug))


def ensure_city(slug: str, name: str, state: str) -> str:
    """CITY has no admin endpoint (§4.1) -- direct upsert, same
    technique as seed.py."""
    city_id = seed_id(slug)
    conn = psycopg2.connect(f"{PG_BASE}/theatre_db", cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO city (id, name, state) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET name = %s, state = %s",
                (city_id, name, state, name, state),
            )
        conn.commit()
    finally:
        conn.close()
    return city_id


def create_theatre(client: httpx.Client, city_id: str, name: str, city_name: str) -> str:
    resp = client.post("/theatre/admin/theatres", json={"city_id": city_id, "name": name, "address": f"{name}, {city_name}"})
    resp.raise_for_status()
    return resp.json()["id"]


def create_screen(client: httpx.Client, theatre_id: str, name: str) -> str:
    resp = client.post(f"/theatre/admin/theatres/{theatre_id}/screens", json={"name": name})
    resp.raise_for_status()
    return resp.json()["id"]


# --- seat layout generation -- same math as admin-web's
# src/lib/placementTools.ts grid/curve tools, reimplemented here in
# Python since this script has no dependency on the frontend. ---


def grid_seats(row_letter_start: str, rows: int, cols: int, seat_type: str, price_multiplier: float, y_start: float) -> list[dict]:
    seats = []
    for r in range(rows):
        row_label = chr(ord(row_letter_start) + r)
        for c in range(cols):
            seats.append(
                {
                    "id": str(uuid.uuid4()),
                    "label": f"{row_label}{c + 1}",
                    "x": float(c),
                    "y": y_start + r,
                    "seat_type": seat_type,
                    "price_multiplier": price_multiplier,
                }
            )
    return seats


def curve_seats(
    row_label: str, count: int, x1: float, y1: float, cx: float, cy: float, x2: float, y2: float, seat_type: str, price_multiplier: float
) -> list[dict]:
    seats = []
    for i in range(count):
        t = 0.5 if count == 1 else i / (count - 1)
        x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2
        y = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2
        seats.append(
            {
                "id": str(uuid.uuid4()),
                "label": f"{row_label}{i + 1}",
                "x": round(x, 2),
                "y": round(y, 2),
                "seat_type": seat_type,
                "price_multiplier": price_multiplier,
            }
        )
    return seats


def build_layout(layout_key: str) -> list[dict]:
    """Six genuinely distinct layouts -- different dimensions on every
    screen, a mix of STANDARD/PREMIUM/RECLINER zones, two using a
    curved back row, so manual testing actually exercises variety
    rather than six copies of the same grid."""
    if layout_key == "t1s1_plain_grid":
        return grid_seats("A", rows=6, cols=10, seat_type="STANDARD", price_multiplier=1.0, y_start=0)

    if layout_key == "t1s2_standard_plus_premium":
        return grid_seats("A", rows=4, cols=8, seat_type="STANDARD", price_multiplier=1.0, y_start=0) + grid_seats(
            "E", rows=2, cols=8, seat_type="PREMIUM", price_multiplier=1.5, y_start=4
        )

    if layout_key == "t2s1_wide_grid":
        return grid_seats("A", rows=8, cols=6, seat_type="STANDARD", price_multiplier=1.0, y_start=0)

    if layout_key == "t2s2_standard_plus_recliner_curve":
        standard = grid_seats("A", rows=5, cols=9, seat_type="STANDARD", price_multiplier=1.0, y_start=0)
        recliners = curve_seats(
            "F", count=7, x1=0, y1=6, cx=4, cy=7.5, x2=8, y2=6, seat_type="RECLINER", price_multiplier=2.0
        )
        return standard + recliners

    if layout_key == "t3s1_square_grid":
        return grid_seats("A", rows=7, cols=7, seat_type="STANDARD", price_multiplier=1.0, y_start=0)

    if layout_key == "t3s2_three_tier":
        standard = grid_seats("A", rows=3, cols=10, seat_type="STANDARD", price_multiplier=1.0, y_start=0)
        premium = grid_seats("D", rows=2, cols=10, seat_type="PREMIUM", price_multiplier=1.5, y_start=3)
        recliners = curve_seats(
            "F", count=8, x1=0, y1=6, cx=4.5, cy=7.5, x2=9, y2=6, seat_type="RECLINER", price_multiplier=2.0
        )
        return standard + premium + recliners

    if layout_key == "mumbai_s1_plain_grid":
        return grid_seats("A", rows=5, cols=8, seat_type="STANDARD", price_multiplier=1.0, y_start=0)

    raise ValueError(f"unknown layout_key: {layout_key}")


def publish_seat_layout(client: httpx.Client, screen_id: str, name: str, layout_key: str) -> dict:
    seats = build_layout(layout_key)
    draft_resp = client.post("/theatre/admin/seat-layouts/draft", json={"screen_id": screen_id, "name": name, "seats": seats})
    draft_resp.raise_for_status()
    draft = draft_resp.json()

    lock_resp = client.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/lock", headers=ADMIN_HEADERS)
    lock_resp.raise_for_status()

    publish_resp = client.post(f"/theatre/admin/seat-layouts/draft/{draft['id']}/publish", headers=ADMIN_HEADERS)
    publish_resp.raise_for_status()
    published = publish_resp.json()
    print(f"    seat layout '{name}': {len(seats)} seats published ({layout_key})")
    return published


def create_movie(client: httpx.Client, title: str, description: str, duration_minutes: int, language: str) -> str:
    resp = client.post(
        "/catalog/admin/movies",
        json={"title": title, "description": description, "duration_minutes": duration_minutes, "language": language},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def create_release(client: httpx.Client, movie_id: str, city_id: str) -> None:
    today = datetime.now(timezone.utc).date()
    resp = client.post(
        f"/catalog/admin/movies/{movie_id}/releases",
        json={
            "city_id": city_id,
            "release_date": (today - timedelta(days=7)).isoformat(),
            "planned_end_date": (today + timedelta(days=90)).isoformat(),
        },
    )
    resp.raise_for_status()


def create_and_activate_showtime(
    client: httpx.Client, movie_id: str, movie_title: str, screen_id: str, start_time: datetime, base_price: float
) -> dict:
    resp = client.post(
        "/theatre/admin/showtimes",
        json={
            "movie_id": movie_id,
            "movie_title": movie_title,
            "screen_id": screen_id,
            "start_time": start_time.isoformat(),
            "base_price": base_price,
        },
    )
    resp.raise_for_status()
    showtime = resp.json()

    activate_resp = client.post(f"/theatre/admin/showtimes/{showtime['id']}/activate")
    activate_resp.raise_for_status()
    return activate_resp.json()


def main() -> None:
    with httpx.Client(base_url=ROUTING_BASE, timeout=15.0) as client:
        try:
            client.get("/theatre/cities")
        except httpx.TransportError as exc:
            raise SystemExit(
                f"Could not reach the routing service at {ROUTING_BASE} -- is the dev stack running (scripts/dev.sh)? ({exc})"
            )

        city_id = ensure_city("city:bengaluru", "Bengaluru", "Karnataka")
        print(f"City: Bengaluru ({city_id})")

        movie_one_id = create_movie(client, "The Last Frame", "A director's final cut becomes a citywide mystery.", 138, "English")
        movie_two_id = create_movie(client, "Monsoon Drift", "Two strangers, one flooded highway, one long night.", 121, "Hindi")
        movie_three_id = create_movie(
            client, "Glass Horizon", "A glassmaker's apprentice uncovers a citywide conspiracy in shards of memory.", 132, "English"
        )
        movie_four_id = create_movie(
            client, "Dilli Dhadkan", "A delivery rider chases one last fare through Delhi's longest night.", 145, "Hindi"
        )
        for movie_id in (movie_one_id, movie_two_id, movie_three_id, movie_four_id):
            create_release(client, movie_id, city_id)
        print(
            f"Movies: 'The Last Frame' ({movie_one_id}), 'Monsoon Drift' ({movie_two_id}), "
            f"'Glass Horizon' ({movie_three_id}), 'Dilli Dhadkan' ({movie_four_id})"
        )

        theatre_specs = [
            ("PVR Orion Mall", "t1s1_plain_grid", "t1s2_standard_plus_premium"),
            ("INOX Mantri Square", "t2s1_wide_grid", "t2s2_standard_plus_recliner_curve"),
            ("Cinepolis Nexus Koramangala", "t3s1_square_grid", "t3s2_three_tier"),
        ]

        base_start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
        run_start_day = base_start.replace(hour=0, minute=0, second=0, microsecond=0)

        # Two movies share each screen, each taking every other slot in
        # SCREEN_HOURS so the two never collide on the same start time.
        # Same assignment on every theatre (the seat layouts already
        # vary per theatre/screen; which movies show where doesn't need
        # to).
        screen1_movies = [
            (movie_one_id, "The Last Frame", 220.0, SCREEN_HOURS[0::2]),
            (movie_three_id, "Glass Horizon", 200.0, SCREEN_HOURS[1::2]),
        ]
        screen2_movies = [
            (movie_two_id, "Monsoon Drift", 180.0, SCREEN_HOURS[0::2]),
            (movie_four_id, "Dilli Dhadkan", 190.0, SCREEN_HOURS[1::2]),
        ]

        def seed_screen_run(screen_id: str, screen_movies: list[tuple[str, str, float, tuple[int, ...]]]) -> int:
            count = 0
            for day_offset in range(ALL_MOVIE_RUN_DAYS):
                day = run_start_day + timedelta(days=day_offset)
                for movie_id, title, base_price, hours in screen_movies:
                    for hour in hours:
                        create_and_activate_showtime(client, movie_id, title, screen_id, day.replace(hour=hour), base_price)
                        count += 1
            return count

        total_bengaluru_showtimes = 0
        for theatre_name, screen1_layout, screen2_layout in theatre_specs:
            print(f"\nTheatre: {theatre_name}")
            theatre_id = create_theatre(client, city_id, theatre_name, "Bengaluru")

            screen1_id = create_screen(client, theatre_id, "Screen 1")
            screen2_id = create_screen(client, theatre_id, "Screen 2")
            publish_seat_layout(client, screen1_id, f"{theatre_name} - Screen 1 layout", screen1_layout)
            publish_seat_layout(client, screen2_id, f"{theatre_name} - Screen 2 layout", screen2_layout)

            count_one = seed_screen_run(screen1_id, screen1_movies)
            count_two = seed_screen_run(screen2_id, screen2_movies)
            total_bengaluru_showtimes += count_one + count_two
            print(
                f"    {ALL_MOVIE_RUN_DAYS} dates x {SHOWS_PER_DAY} shows/date: "
                f"{count_one} showtimes on Screen 1 ('The Last Frame' + 'Glass Horizon'), "
                f"{count_two} showtimes on Screen 2 ('Monsoon Drift' + 'Dilli Dhadkan')"
            )

        # Second city, deliberately lighter-weight -- exists so this
        # script alone satisfies the regression suite's multi-city
        # assumption (test_phase1.py::test_movies_browse_is_city_scoped)
        # without needing a separate seed.py run afterward. Only "The
        # Last Frame" is released here, none of the other three movies
        # -- that's what makes the per-city movie set actually differ,
        # which the same test also asserts, not just the city count.
        mumbai_city_id = ensure_city("city:mumbai", "Mumbai", "Maharashtra")
        print(f"\nCity: Mumbai ({mumbai_city_id}) -- secondary, for multi-city coverage")
        create_release(client, movie_one_id, mumbai_city_id)
        mumbai_theatre_id = create_theatre(client, mumbai_city_id, "PVR Phoenix Mills", "Mumbai")
        mumbai_screen_id = create_screen(client, mumbai_theatre_id, "Screen 1")
        publish_seat_layout(client, mumbai_screen_id, "PVR Phoenix Mills - Screen 1 layout", "mumbai_s1_plain_grid")
        mumbai_showtime = create_and_activate_showtime(
            client, movie_one_id, "The Last Frame", mumbai_screen_id, base_start, base_price=250.0
        )
        print(f"    showtime: 'The Last Frame' on Screen 1 at {base_start.isoformat()} (id {mumbai_showtime['id']})")

        print(
            "\nManual-testing seed complete: Bengaluru (3 theatres, 6 screens, 6 distinct seat layouts, 4 movies, "
            f"{total_bengaluru_showtimes} active showtimes -- every movie running in every theatre for "
            f"{ALL_MOVIE_RUN_DAYS} dates x {SHOWS_PER_DAY} shows/date) + Mumbai (1 theatre, 1 screen, 1 movie, "
            "1 active showtime)."
        )


if __name__ == "__main__":
    main()
