"""Seed data — Phase 1.

Populates a small realistic dataset: a few cities, theatres, screens,
movies, and per-city releases. Idempotent -- safe to run repeatedly
against an already-seeded database, the same way a real seed tool would
in any environment. Writes directly to each service's database (not
through the HTTP API) since CITY in particular has no admin endpoint at
this phase (absent from Appendix C).

Run with the same Postgres the dev stack uses:
    python infra/seed/seed.py
"""
import os
import uuid

import psycopg2
import psycopg2.extras

PG_USER = os.getenv("POSTGRES_USER", "movieticket")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "movieticket_dev_password")
PG_HOST_PORT = os.getenv("POSTGRES_HOST_PORT", "5433")
PG_BASE = f"postgresql://{PG_USER}:{PG_PASSWORD}@localhost:{PG_HOST_PORT}"

# Deterministic UUIDs (uuid5 over a fixed namespace + a human-readable
# slug) so re-running this script always derives the same ids -- that's
# what makes the upserts below idempotent across runs, including across
# separate catalog_db/theatre_db connections that need to agree on the
# same city_id without a shared sequence.
SEED_NAMESPACE = uuid.UUID("9f9e9c1a-0000-4000-8000-000000000001")


def seed_id(slug: str) -> str:
    return str(uuid.uuid5(SEED_NAMESPACE, slug))


CITIES = [
    {"slug": "city:mumbai", "name": "Mumbai", "state": "Maharashtra"},
    {"slug": "city:bengaluru", "name": "Bengaluru", "state": "Karnataka"},
    {"slug": "city:delhi", "name": "Delhi", "state": "Delhi"},
]

THEATRES = [
    {"slug": "theatre:pvr-phoenix", "city_slug": "city:mumbai", "name": "PVR Phoenix Mills", "address": "Lower Parel, Mumbai"},
    {"slug": "theatre:inox-forum", "city_slug": "city:bengaluru", "name": "INOX Forum Mall", "address": "Koramangala, Bengaluru"},
    {"slug": "theatre:pvr-select-city", "city_slug": "city:delhi", "name": "PVR Select Citywalk", "address": "Saket, Delhi"},
]

SCREENS = [
    {"slug": "screen:pvr-phoenix-1", "theatre_slug": "theatre:pvr-phoenix", "name": "Screen 1"},
    {"slug": "screen:pvr-phoenix-2", "theatre_slug": "theatre:pvr-phoenix", "name": "Screen 2"},
    {"slug": "screen:inox-forum-1", "theatre_slug": "theatre:inox-forum", "name": "Screen 1"},
    {"slug": "screen:pvr-select-city-1", "theatre_slug": "theatre:pvr-select-city", "name": "Screen 1"},
]

MOVIES = [
    {"slug": "movie:interstellar-voyage", "title": "Interstellar Voyage", "description": "A crew races against time across the stars.", "duration_minutes": 148, "language": "English"},
    {"slug": "movie:monsoon-wedding-tales", "title": "Monsoon Wedding Tales", "description": "Three families, one wedding season.", "duration_minutes": 132, "language": "Hindi"},
    {"slug": "movie:silent-circuit", "title": "Silent Circuit", "description": "A hacker uncovers a conspiracy in one sleepless night.", "duration_minutes": 121, "language": "English"},
]

RELEASES = [
    {"movie_slug": "movie:interstellar-voyage", "city_slug": "city:mumbai", "release_date": "2026-05-01", "planned_end_date": "2026-08-01"},
    {"movie_slug": "movie:interstellar-voyage", "city_slug": "city:bengaluru", "release_date": "2026-05-01", "planned_end_date": "2026-08-01"},
    {"movie_slug": "movie:monsoon-wedding-tales", "city_slug": "city:mumbai", "release_date": "2026-04-15", "planned_end_date": "2026-07-15"},
    {"movie_slug": "movie:silent-circuit", "city_slug": "city:delhi", "release_date": "2026-06-01", "planned_end_date": "2026-09-01"},
]


def upsert(conn, table: str, row_id: str, columns: dict) -> None:
    col_names = ["id"] + list(columns.keys())
    placeholders = ["%(id)s"] + [f"%({c})s" for c in columns]
    update_clause = ", ".join(f"{c} = %({c})s" for c in columns)
    params = {"id": row_id, **columns}
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({', '.join(col_names)}) VALUES ({', '.join(placeholders)}) "
            f"ON CONFLICT (id) DO UPDATE SET {update_clause}",
            params,
        )
    conn.commit()


def seed_theatre_db() -> None:
    conn = psycopg2.connect(f"{PG_BASE}/theatre_db", cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        for city in CITIES:
            upsert(conn, "city", seed_id(city["slug"]), {"name": city["name"], "state": city["state"]})
        for theatre in THEATRES:
            upsert(
                conn,
                "theatre",
                seed_id(theatre["slug"]),
                {
                    "city_id": seed_id(theatre["city_slug"]),
                    "name": theatre["name"],
                    "address": theatre["address"],
                },
            )
        for screen in SCREENS:
            upsert(
                conn,
                "screen",
                seed_id(screen["slug"]),
                {"theatre_id": seed_id(screen["theatre_slug"]), "name": screen["name"]},
            )
        print(f"theatre_db: {len(CITIES)} cities, {len(THEATRES)} theatres, {len(SCREENS)} screens")
    finally:
        conn.close()


def seed_catalog_db() -> None:
    conn = psycopg2.connect(f"{PG_BASE}/catalog_db", cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        for movie in MOVIES:
            upsert(
                conn,
                "movie",
                seed_id(movie["slug"]),
                {
                    "title": movie["title"],
                    "description": movie["description"],
                    "duration_minutes": movie["duration_minutes"],
                    "language": movie["language"],
                },
            )
        for release in RELEASES:
            upsert(
                conn,
                "movie_release",
                seed_id(f"release:{release['movie_slug']}:{release['city_slug']}"),
                {
                    "movie_id": seed_id(release["movie_slug"]),
                    "city_id": seed_id(release["city_slug"]),
                    "release_date": release["release_date"],
                    "planned_end_date": release["planned_end_date"],
                },
            )
        print(f"catalog_db: {len(MOVIES)} movies, {len(RELEASES)} releases")
    finally:
        conn.close()


if __name__ == "__main__":
    seed_theatre_db()
    seed_catalog_db()
    print("Seed complete.")
