"""Customer-facing theatre endpoints (Appendix A) -- browse only, no
writes. `GET /theatres?city=` and `GET /cities` fill gaps Appendix A
originally left for real city-scoped theatre discovery (Phase 1/8).
"""
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException

from common.config import CATALOG_SERVICE_URL
from common.db import get_db
from common.theatres import get_theatre_or_404

router = APIRouter()


@router.get("/cities")
def list_cities(conn=Depends(get_db)) -> list[dict]:
    """Not in Appendix A's original contract -- added Phase 8: the
    customer SPA needs a human-readable city picker, and CITY is
    theatre-owned (§4.1) with no existing read endpoint at all (`GET
    /theatres?city=` takes a city_id but never exposes one to pick from)."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM city ORDER BY name")
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/theatres")
def list_theatres(city: Optional[UUID] = None, conn=Depends(get_db)) -> list[dict]:
    if city is not None:
        sql = "SELECT * FROM theatre WHERE city_id = %s ORDER BY name"
        params = (str(city),)
    else:
        sql = "SELECT * FROM theatre ORDER BY name"
        params = ()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/theatres/{theatre_id}")
def get_theatre(theatre_id: UUID, conn=Depends(get_db)) -> dict:
    return get_theatre_or_404(conn, theatre_id)


@router.get("/showtimes/{showtime_id}")
def get_showtime(showtime_id: UUID, conn=Depends(get_db)) -> dict:
    """Plain, theatre-only (no cross-service enrichment) -- low-traffic
    standalone lookup, e.g. a refresh/sanity check before seat selection.
    The richer, cached showtime context lives on booking's seatmap
    response (Phase 8, design v16) for the actual high-volume read path."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT st.*, s.name AS screen_name, t.name AS theatre_name, t.id AS theatre_id "
            "FROM showtime st JOIN screen s ON s.id = st.screen_id JOIN theatre t ON t.id = s.theatre_id "
            "WHERE st.id = %s",
            (str(showtime_id),),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="showtime not found")
    return dict(row)


@router.get("/movies/{movie_id}/showtimes")
def list_showtimes_for_movie(
    movie_id: UUID,
    city: Optional[UUID] = None,
    date: Optional[str] = None,
    conn=Depends(get_db),
) -> dict:
    """Appendix A, enriched (Phase 8, design v16): one response carrying
    both the movie's own details (via a single, low-frequency
    cross-service call to catalog -- this is picked once per date/city
    choice, not once per click, unlike the seatmap read) and the matching
    showtime list, so the frontend doesn't have to orchestrate two calls
    itself for this page."""
    sql = (
        "SELECT st.*, s.name AS screen_name, t.name AS theatre_name "
        "FROM showtime st JOIN screen s ON s.id = st.screen_id JOIN theatre t ON t.id = s.theatre_id "
        "WHERE st.movie_id = %(movie_id)s AND st.is_active = true"
    )
    params: dict = {"movie_id": str(movie_id)}
    if city is not None:
        sql += " AND t.city_id = %(city)s"
        params["city"] = str(city)
    if date is not None:
        sql += " AND st.start_time::date = %(date)s"
        params["date"] = date
    sql += " ORDER BY st.start_time"

    with conn.cursor() as cur:
        cur.execute(sql, params)
        showtimes = [dict(r) for r in cur.fetchall()]

    try:
        resp = httpx.get(f"{CATALOG_SERVICE_URL}/movies/{movie_id}", timeout=5.0)
    except httpx.TransportError as exc:
        raise HTTPException(status_code=503, detail=f"catalog service unavailable: {exc}")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="movie not found")
    resp.raise_for_status()

    return {"movie": resp.json(), "showtimes": showtimes}
