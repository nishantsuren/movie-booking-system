"""Phase 0 verification: 'Shared idempotency middleware has unit tests
proving the INSERT ... ON CONFLICT pattern behaves correctly against a
throwaway table' (implementation plan, Phase 0).

Run against a real, disposable Postgres database — not mocked — because
the whole point of this pattern (§11.1) is a guarantee that only holds if
the database actually enforces it.
"""
import os
import uuid

import psycopg2
import pytest

from idempotency.idempotency import IdempotentWriter

DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://movieticket:movieticket_dev_password@localhost:5433/catalog_db",
)


@pytest.fixture
def conn():
    connection = psycopg2.connect(DB_URL)
    with connection.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS throwaway_booking (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                idempotency_key TEXT UNIQUE NOT NULL,
                showtime_id TEXT NOT NULL,
                seat_labels TEXT NOT NULL
            );
        """)
    connection.commit()
    yield connection
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS throwaway_booking;")
    connection.commit()
    connection.close()


def test_first_call_creates_a_row(conn):
    writer = IdempotentWriter(conn)
    key = str(uuid.uuid4())

    row, was_created = writer.insert_or_get(
        "throwaway_booking",
        {"idempotency_key": key, "showtime_id": "st-1", "seat_labels": "A1,A2"},
    )

    assert was_created is True
    assert row["idempotency_key"] == key
    assert row["seat_labels"] == "A1,A2"


def test_repeated_call_with_same_key_returns_original_row_not_a_duplicate(conn):
    writer = IdempotentWriter(conn)
    key = str(uuid.uuid4())

    first_row, first_created = writer.insert_or_get(
        "throwaway_booking",
        {"idempotency_key": key, "showtime_id": "st-1", "seat_labels": "A1,A2"},
    )

    # Same key, deliberately different payload — simulating a client retry
    # that, for whatever reason, sent slightly different data the second
    # time. The original row must win; nothing should be overwritten.
    second_row, second_created = writer.insert_or_get(
        "throwaway_booking",
        {"idempotency_key": key, "showtime_id": "st-1", "seat_labels": "DIFFERENT,PAYLOAD"},
    )

    assert second_created is False
    assert second_row["id"] == first_row["id"]
    assert second_row["seat_labels"] == "A1,A2", "must return the ORIGINAL row, not re-execute the write"

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM throwaway_booking WHERE idempotency_key = %s", (key,))
        count = cur.fetchone()[0]
    assert count == 1, "no duplicate row should exist"


def test_different_keys_create_separate_rows(conn):
    writer = IdempotentWriter(conn)

    row_a, created_a = writer.insert_or_get(
        "throwaway_booking",
        {"idempotency_key": str(uuid.uuid4()), "showtime_id": "st-1", "seat_labels": "A1"},
    )
    row_b, created_b = writer.insert_or_get(
        "throwaway_booking",
        {"idempotency_key": str(uuid.uuid4()), "showtime_id": "st-1", "seat_labels": "A2"},
    )

    assert created_a is True
    assert created_b is True
    assert row_a["id"] != row_b["id"]
