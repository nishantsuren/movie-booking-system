"""Plain psycopg2 connection-per-request, per CLAUDE.md's DB-access
convention (no ORM, no pooling at this phase's traffic volume)."""
import os
from typing import Iterator

import psycopg2
import psycopg2.extras


def get_db() -> Iterator[psycopg2.extensions.connection]:
    conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()
