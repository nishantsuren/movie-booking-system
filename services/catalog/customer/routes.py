"""Customer-facing catalog endpoints (Appendix A) -- browse only, no
writes. Soft-deleted (`is_active=FALSE`) movies never show up here
(§4.2); admin's own listing (admin/routes.py) is the one that needs to
see everything.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends

from common.db import get_db
from common.movies import get_movie_or_404

router = APIRouter()


@router.get("/movies")
def list_movies(city: Optional[UUID] = None, conn=Depends(get_db)) -> list[dict]:
    """Active movies, optionally scoped to a city's currently-running
    release window (§4.4). `city` is theatre service's city_id, used as a
    loose reference directly -- no local CITY copy at this phase."""
    if city is not None:
        sql = """
            SELECT DISTINCT m.*
            FROM movie m
            JOIN movie_release r ON r.movie_id = m.id
            WHERE m.is_active = TRUE
              AND r.city_id = %s
              AND r.release_date <= CURRENT_DATE
              AND COALESCE(r.actual_end_date, r.planned_end_date, 'infinity'::date) >= CURRENT_DATE
            ORDER BY m.title
        """
        params = (str(city),)
    else:
        sql = "SELECT * FROM movie WHERE is_active = TRUE ORDER BY title"
        params = ()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/movies/{movie_id}")
def get_movie(movie_id: UUID, conn=Depends(get_db)) -> dict:
    """Resolves regardless of is_active -- soft-delete affects browse
    visibility only, never direct-by-ID lookup (§4.2)."""
    return get_movie_or_404(conn, movie_id)
