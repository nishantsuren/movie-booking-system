"""Catalog service — Phase 1.

MOVIE + MOVIE_RELEASE (§4.4), customer browse (Appendix A) and admin CRUD
(Appendix C). Soft-delete is genuine (`is_active`, §4.2) — never
cascades, never hard-deletes.
"""
import hashlib
import os
from datetime import date, datetime
from typing import Optional
from uuid import UUID

import psycopg2
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from db import get_db
from shared.auth.auth import AuthContext, get_auth_context, require_role
from shared.idempotency.idempotency import IdempotentWriter

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

app = FastAPI(title="Catalog service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "catalog", "auth_enabled": AUTH_ENABLED}


# --- request bodies ---

class MovieCreate(BaseModel):
    title: str
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    language: Optional[str] = None
    poster_asset_id: Optional[UUID] = None


class MovieUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    duration_minutes: Optional[int] = None
    language: Optional[str] = None
    poster_asset_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class MovieReleaseCreate(BaseModel):
    city_id: UUID
    release_date: date
    planned_end_date: Optional[date] = None
    actual_end_date: Optional[date] = None


class MovieReleaseUpdate(BaseModel):
    release_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    actual_end_date: Optional[date] = None


def _derive_idempotency_key(*parts: object) -> str:
    """Deterministic dedup key derived from a create request's
    identity-defining fields (§11.1) -- no client-managed Idempotency-Key
    header. Resubmitting the same logical create (a genuine retry, or an
    accidental double-submit) always re-derives the same key and is
    deduplicated automatically by the unique constraint on this column.

    Trade-off accepted deliberately: two distinct entities that happen to
    share every identity-defining field collide into one row. That's the
    cost of not requiring an explicit caller-supplied key.
    """
    normalized = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _get_movie_or_404(conn, movie_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM movie WHERE id = %s", (str(movie_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="movie not found")
    return dict(row)


# --- customer endpoints (Appendix A) ---

@app.get("/movies")
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


@app.get("/movies/{movie_id}")
def get_movie(movie_id: UUID, conn=Depends(get_db)) -> dict:
    """Resolves regardless of is_active -- soft-delete affects browse
    visibility only, never direct-by-ID lookup (§4.2)."""
    return _get_movie_or_404(conn, movie_id)


# --- admin endpoints (Appendix C) ---

@app.post("/admin/movies", status_code=201)
def create_movie(
    body: MovieCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    idempotency_key = _derive_idempotency_key(body.title, body.duration_minutes, body.language)
    writer = IdempotentWriter(conn)
    row, _created = writer.insert_or_get(
        "movie",
        {
            "idempotency_key": idempotency_key,
            "title": body.title,
            "description": body.description,
            "duration_minutes": body.duration_minutes,
            "language": body.language,
            "poster_asset_id": str(body.poster_asset_id) if body.poster_asset_id else None,
        },
    )
    return row


@app.put("/admin/movies/{movie_id}")
def update_movie(
    movie_id: UUID,
    body: MovieUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    _get_movie_or_404(conn, movie_id)
    fields = body.model_dump(exclude_unset=True)
    if "poster_asset_id" in fields and fields["poster_asset_id"] is not None:
        fields["poster_asset_id"] = str(fields["poster_asset_id"])
    if not fields:
        return _get_movie_or_404(conn, movie_id)

    set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
    fields["movie_id"] = str(movie_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE movie SET {set_clause}, updated_at = now() WHERE id = %(movie_id)s RETURNING *",
            fields,
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row)


@app.delete("/admin/movies/{movie_id}", status_code=204)
def delete_movie(
    movie_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> None:
    """Genuine soft-delete (§4.2) -- flips is_active, never cascades to
    anything else, never removes the row."""
    _get_movie_or_404(conn, movie_id)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE movie SET is_active = FALSE, updated_at = now() WHERE id = %s",
            (str(movie_id),),
        )
    conn.commit()


@app.post("/admin/movies/{movie_id}/releases", status_code=201)
def create_release(
    movie_id: UUID,
    body: MovieReleaseCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    _get_movie_or_404(conn, movie_id)
    idempotency_key = _derive_idempotency_key(movie_id, body.city_id, body.release_date)
    writer = IdempotentWriter(conn)
    row, _created = writer.insert_or_get(
        "movie_release",
        {
            "idempotency_key": idempotency_key,
            "movie_id": str(movie_id),
            "city_id": str(body.city_id),
            "release_date": body.release_date,
            "planned_end_date": body.planned_end_date,
            "actual_end_date": body.actual_end_date,
        },
    )
    return row


@app.put("/admin/releases/{release_id}")
def update_release(
    release_id: UUID,
    body: MovieReleaseUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM movie_release WHERE id = %s", (str(release_id),))
        existing = cur.fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="release not found")
    if not fields:
        return dict(existing)

    set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
    fields["release_id"] = str(release_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE movie_release SET {set_clause}, updated_at = now() WHERE id = %(release_id)s RETURNING *",
            fields,
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row)
