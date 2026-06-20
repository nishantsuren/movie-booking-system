"""SHOWTIME_META cache (§4.3/§11.1 v12) -- movie_title, upserted once per
showtime at materialize time, read at booking-creation time. Avoids any
live cross-service call on the booking hot path.
"""
from typing import Optional


class ShowtimeMetaRepository:
    def __init__(self, conn):
        self._conn = conn

    def upsert(self, showtime_id: str, movie_title: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO showtime_meta (showtime_id, movie_title)
                VALUES (%(showtime_id)s, %(movie_title)s)
                ON CONFLICT (showtime_id) DO UPDATE SET movie_title = excluded.movie_title, updated_at = now()
                """,
                {"showtime_id": showtime_id, "movie_title": movie_title},
            )

    def get_movie_title(self, showtime_id: str) -> Optional[str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT movie_title FROM showtime_meta WHERE showtime_id = %s", (showtime_id,))
            row = cur.fetchone()
        return row["movie_title"] if row else None
