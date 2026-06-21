"""SHOWTIME_META cache (§4.3/§11.1 v12, extended Phase 8) -- showtime
display context (movie_title, theatre_name, screen_name, start_time,
base_price) upserted once per showtime at materialize time, read at
booking-creation time and by the seatmap endpoint. Avoids any live
cross-service call on either of those hot paths.
"""
from typing import Optional


class ShowtimeMetaRepository:
    def __init__(self, conn):
        self._conn = conn

    def upsert(
        self,
        showtime_id: str,
        movie_title: str,
        theatre_name: str = "",
        screen_name: str = "",
        start_time=None,
        base_price: Optional[float] = None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO showtime_meta (showtime_id, movie_title, theatre_name, screen_name, start_time, base_price)
                VALUES (%(showtime_id)s, %(movie_title)s, %(theatre_name)s, %(screen_name)s, %(start_time)s, %(base_price)s)
                ON CONFLICT (showtime_id) DO UPDATE SET
                    movie_title = excluded.movie_title,
                    theatre_name = excluded.theatre_name,
                    screen_name = excluded.screen_name,
                    start_time = excluded.start_time,
                    base_price = excluded.base_price,
                    updated_at = now()
                """,
                {
                    "showtime_id": showtime_id,
                    "movie_title": movie_title,
                    "theatre_name": theatre_name,
                    "screen_name": screen_name,
                    "start_time": start_time,
                    "base_price": base_price,
                },
            )

    def get_movie_title(self, showtime_id: str) -> Optional[str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT movie_title FROM showtime_meta WHERE showtime_id = %s", (showtime_id,))
            row = cur.fetchone()
        return row["movie_title"] if row else None

    def get(self, showtime_id: str) -> Optional[dict]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM showtime_meta WHERE showtime_id = %s", (showtime_id,))
            row = cur.fetchone()
        return dict(row) if row else None
