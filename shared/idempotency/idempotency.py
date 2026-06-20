"""Database-enforced idempotency (design doc §11.1).

No shared Redis store, no separate check-then-write race window. The
duplicate-check and the write are the same atomic database operation, via
INSERT ... ON CONFLICT against a unique constraint already on the target
table. Each service uses this against its own database — there is no
cross-service idempotency store (§11.2).
"""
import time

import psycopg2
import psycopg2.extras


class IdempotencyConflict(RuntimeError):
    """Raised if a conflicting row never becomes visible after retrying —
    should not happen under normal operation; see the bounded retry-read
    loop below for the one legitimate race this guards against."""


class IdempotentWriter:
    def __init__(self, conn: "psycopg2.extensions.connection"):
        self._conn = conn

    def insert_or_get(
        self,
        table: str,
        columns: dict,
        idempotency_key_column: str = "idempotency_key",
        max_read_retries: int = 5,
        retry_delay_seconds: float = 0.02,
    ) -> tuple[dict, bool]:
        """Insert a row, or return the existing one if the idempotency key
        was already used. Returns (row, was_created).

        The retry-read loop handles one narrow, well-understood race: a
        retried request arriving while the original transaction that owns
        this key hasn't committed yet, so the conflict is detected but the
        row isn't visible yet under read-committed isolation (§11.1).
        """
        col_names = list(columns.keys())
        placeholders = [f"%({c})s" for c in col_names]
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(col_names)}) "
            f"VALUES ({', '.join(placeholders)}) "
            f"ON CONFLICT ({idempotency_key_column}) DO NOTHING "
            f"RETURNING *;"
        )

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(insert_sql, columns)
            row = cur.fetchone()
            if row is not None:
                self._conn.commit()
                return dict(row), True
            self._conn.commit()

        key_value = columns[idempotency_key_column]
        for _ in range(max_read_retries):
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM {table} WHERE {idempotency_key_column} = %s",
                    (key_value,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return dict(existing), False
            time.sleep(retry_delay_seconds)

        raise IdempotencyConflict(
            f"idempotency key {key_value!r} conflicted on {table} but no row "
            "became visible after retrying"
        )
