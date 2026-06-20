"""Applies numbered SQL migration files to one service database, tracked
via a schema_migrations table (CLAUDE.md migrations convention) — no ORM,
no migration framework.

Usage:
    python run_migrations.py <service> <database_url>

Looks for SQL files in infra/migrations/<service>/NNN_*.sql, applies any
not yet recorded in that database's schema_migrations table, in filename
order, each in its own transaction.
"""
import sys
from pathlib import Path

import psycopg2

MIGRATIONS_ROOT = Path(__file__).parent


def run(service: str, database_url: str) -> None:
    service_dir = MIGRATIONS_ROOT / service
    files = sorted(service_dir.glob("*.sql"))
    if not files:
        print(f"No migration files found for service {service!r} in {service_dir}")
        return

    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}

        for path in files:
            if path.name in applied:
                continue
            print(f"Applying {service}/{path.name}...")
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            conn.commit()
        print(f"{service}: up to date ({len(files)} migration(s) total).")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python run_migrations.py <service> <database_url>", file=sys.stderr)
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
