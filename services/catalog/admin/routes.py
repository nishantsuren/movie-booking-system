"""Admin-only catalog endpoints (Appendix C) -- MOVIE and MOVIE_RELEASE
CRUD, role-gated via require_role("ADMIN"). Soft-delete only (§4.2):
DELETE flips `is_active`, never removes a row, never cascades.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from admin.schemas import MovieCreate, MovieReleaseCreate, MovieReleaseUpdate, MovieUpdate
from common.db import get_db
from common.idempotency import derive_idempotency_key
from common.movies import get_movie_or_404
from shared.auth.auth import AuthContext, require_role
from shared.idempotency.idempotency import IdempotentWriter

router = APIRouter(prefix="/admin")


@router.get("/movies")
def list_movies_for_admin(conn=Depends(get_db), _ctx: AuthContext = Depends(require_role("ADMIN"))) -> list[dict]:
    """Phase 9: admin management needs to see every movie, including
    deactivated ones (to reactivate, or just to know they exist) --
    GET /movies (customer browse) always filters to is_active=TRUE even
    with no city given, so it can't serve this. New endpoint rather than
    an `include_inactive` flag on the customer one, since the two have
    genuinely different audiences and the customer contract (Appendix A)
    shouldn't grow an admin-only parameter."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM movie ORDER BY title")
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/movies", status_code=201)
def create_movie(
    body: MovieCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    idempotency_key = derive_idempotency_key(body.title, body.duration_minutes, body.language)
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


@router.put("/movies/{movie_id}")
def update_movie(
    movie_id: UUID,
    body: MovieUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    get_movie_or_404(conn, movie_id)
    fields = body.model_dump(exclude_unset=True)
    if "poster_asset_id" in fields and fields["poster_asset_id"] is not None:
        fields["poster_asset_id"] = str(fields["poster_asset_id"])
    if not fields:
        return get_movie_or_404(conn, movie_id)

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


@router.delete("/movies/{movie_id}", status_code=204)
def delete_movie(
    movie_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> None:
    """Genuine soft-delete (§4.2) -- flips is_active, never cascades to
    anything else, never removes the row."""
    get_movie_or_404(conn, movie_id)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE movie SET is_active = FALSE, updated_at = now() WHERE id = %s",
            (str(movie_id),),
        )
    conn.commit()


@router.get("/movies/{movie_id}/releases")
def list_releases_for_movie(
    movie_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> list[dict]:
    """Phase 9: the admin edit form needs to show existing releases (to
    edit one, you need its release_id, which only ever existed in a
    create-response until now)."""
    get_movie_or_404(conn, movie_id)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM movie_release WHERE movie_id = %s ORDER BY release_date", (str(movie_id),))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/movies/{movie_id}/releases", status_code=201)
def create_release(
    movie_id: UUID,
    body: MovieReleaseCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    get_movie_or_404(conn, movie_id)
    idempotency_key = derive_idempotency_key(movie_id, body.city_id, body.release_date)
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


@router.put("/releases/{release_id}")
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
