"""MOVIE lookups shared by both customer and admin routes -- the
not-found check is identical regardless of which side is asking.
"""
from uuid import UUID

from fastapi import HTTPException


def get_movie_or_404(conn, movie_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM movie WHERE id = %s", (str(movie_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="movie not found")
    return dict(row)
