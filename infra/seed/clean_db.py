"""Database cleanup for manual testing.

Truncates every application table in every service database (catalog,
theatre, asset, booking, payment, user) -- a full reset, including
CITY, so the next run of seed_manual_testing.py starts from a known,
reproducible state rather than layering on top of whatever was there
before. `schema_migrations` is left untouched in each database (it
tracks which migration files have run, not application data).

Same direct-psycopg2-per-database approach as seed.py, not docker exec
-- works the same whether Postgres happens to be containerized or not.

Run with the same Postgres the dev stack uses:
    python infra/seed/clean_db.py              # clean only
    python infra/seed/clean_db.py --reseed      # clean, then run seed_manual_testing.py
"""
import argparse
import os
import sys

import psycopg2

PG_USER = os.getenv("POSTGRES_USER", "movieticket")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "movieticket_dev_password")
PG_HOST_PORT = os.getenv("POSTGRES_HOST_PORT", "5433")
PG_BASE = f"postgresql://{PG_USER}:{PG_PASSWORD}@localhost:{PG_HOST_PORT}"

# Every application table per database. Listed together (not table-by-
# table) so a single TRUNCATE handles the real FKs between them within
# the same database (e.g. screen -> theatre -> city, showtime_seat ->
# booking) without needing CASCADE for anything other than the
# unenforced cross-service loose references this design deliberately
# never FK-constrains (§4.1).
TABLES_BY_DB = {
    "catalog_db": ["movie_release", "movie"],
    "theatre_db": ["showtime", "seat_template", "seat_layout", "screen", "theatre", "city"],
    "asset_db": ["asset"],
    "booking_db": ["pending_theatre_call", "showtime_seat", "booking", "showtime_meta"],
    "payment_db": ["payment"],
    "user_db": ["app_user"],
}


def truncate_db(db_name: str, tables: list[str]) -> None:
    conn = psycopg2.connect(f"{PG_BASE}/{db_name}")
    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(tables)} CASCADE")
        conn.commit()
        print(f"{db_name}: truncated {len(tables)} table(s) ({', '.join(tables)})")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reseed",
        action="store_true",
        help="run seed_manual_testing.py immediately after cleaning (requires the dev stack to be running)",
    )
    args = parser.parse_args()

    for db_name, tables in TABLES_BY_DB.items():
        truncate_db(db_name, tables)
    print("Database clean complete.")

    if args.reseed:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import seed_manual_testing

        print()
        seed_manual_testing.main()


if __name__ == "__main__":
    main()
