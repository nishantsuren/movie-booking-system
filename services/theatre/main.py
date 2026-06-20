"""Theatre service — Phase 1.

CITY, THEATRE, SCREEN (§4.1) -- not SEAT_LAYOUT/SEAT_TEMPLATE, that's
Phase 2. Customer browse + admin CRUD per Appendix A/C, plus
`GET /theatres?city=` filling a gap Appendix A leaves for real
city-scoped theatre discovery at this phase (showtimes, the more natural
discovery path, don't exist until Phase 3).
"""
import hashlib
import os
from typing import Optional
from uuid import UUID

import psycopg2
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from db import get_db
from shared.auth.auth import AuthContext, get_auth_context, require_role
from shared.idempotency.idempotency import IdempotentWriter

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

# Draft-lock staleness threshold (design §4.6): "~2 minutes, generous
# against network blips" -- a single source of truth shared by every
# lock-gated SQL check below (acquire, publish, PATCH seat(s)).
LOCK_STALE_MINUTES = 2

app = FastAPI(title="Theatre service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "theatre", "auth_enabled": AUTH_ENABLED}


# --- request bodies ---

class TheatreCreate(BaseModel):
    city_id: UUID
    name: str
    address: Optional[str] = None


class TheatreUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None


class ScreenCreate(BaseModel):
    name: str


class ScreenUpdate(BaseModel):
    name: Optional[str] = None


class SeatCreate(BaseModel):
    id: UUID
    label: str
    x: float
    y: float
    seat_type: str
    price_multiplier: float


class SeatLayoutDraftCreate(BaseModel):
    screen_id: UUID
    name: str
    seats: list[SeatCreate]


class SeatPatch(BaseModel):
    label: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    seat_type: Optional[str] = None
    price_multiplier: Optional[float] = None
    is_active: Optional[bool] = None


class BulkSeatPatch(SeatPatch):
    seat_ids: list[UUID]


class CloneRequest(BaseModel):
    target_screen_id: UUID


def _derive_idempotency_key(*parts: object) -> str:
    """Deterministic dedup key derived from a create request's
    identity-defining fields (§11.1) -- see catalog/main.py's version of
    this helper for the full rationale and accepted trade-off."""
    normalized = "|".join(str(p).strip().lower() for p in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _get_theatre_or_404(conn, theatre_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM theatre WHERE id = %s", (str(theatre_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="theatre not found")
    return dict(row)


def _get_admin_identity(
    request: Request, ctx: AuthContext = Depends(get_auth_context)
) -> UUID:
    """Caller identity for draft-lock enforcement (§4.6). Real users/JWTs
    don't exist until Phase 7, so with AUTH_ENABLED=false (today's default)
    get_auth_context always returns user_id=None for every caller -- which
    would make every admin session indistinguishable and the lock
    unenforceable. Fall back to a client-supplied X-Admin-User-Id header in
    that mode; once AUTH_ENABLED=true the JWT's sub claim is authoritative
    and the header is ignored."""
    if AUTH_ENABLED:
        if ctx.user_id is None:
            raise HTTPException(status_code=401, detail="missing user identity")
        try:
            return UUID(ctx.user_id)
        except ValueError:
            raise HTTPException(status_code=401, detail="invalid user identity in token")

    header = request.headers.get("x-admin-user-id")
    if not header:
        raise HTTPException(
            status_code=400,
            detail="X-Admin-User-Id header is required while AUTH_ENABLED=false",
        )
    try:
        return UUID(header)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Admin-User-Id must be a valid UUID")


def _get_layout_or_404(conn, layout_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM seat_layout WHERE id = %s", (str(layout_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="seat layout not found")
    return dict(row)


def _list_seats(conn, layout_id: UUID) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM seat_template WHERE seat_layout_id = %s ORDER BY created_at",
            (str(layout_id),),
        )
        return [dict(r) for r in cur.fetchall()]


def _raise_lock_error(conn, draft_id: UUID, admin_id: UUID) -> None:
    """Called after a lock-gated mutation affects zero rows, to turn that
    into the right error. Re-reads current state fresh -- this function
    itself never assumes the lock is still held, since by construction the
    caller only reaches here after the gated SQL already determined it isn't."""
    layout = _get_layout_or_404(conn, draft_id)
    if layout["status"] != "DRAFT":
        raise HTTPException(status_code=409, detail="layout is not in DRAFT status")
    if layout["locked_by_user_id"] is None or str(layout["locked_by_user_id"]) != str(admin_id):
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "you do not hold the edit lock for this draft",
                "locked_by_user_id": str(layout["locked_by_user_id"]) if layout["locked_by_user_id"] else None,
            },
        )
    raise HTTPException(status_code=403, detail="edit lock has gone stale; re-acquire before editing")


# --- customer endpoints (Appendix A) ---

@app.get("/theatres")
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


@app.get("/theatres/{theatre_id}")
def get_theatre(theatre_id: UUID, conn=Depends(get_db)) -> dict:
    return _get_theatre_or_404(conn, theatre_id)


# --- admin endpoints (Appendix C) ---

@app.post("/admin/theatres", status_code=201)
def create_theatre(
    body: TheatreCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM city WHERE id = %s", (str(body.city_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="city not found")

    idempotency_key = _derive_idempotency_key(body.city_id, body.name)
    writer = IdempotentWriter(conn)
    row, _created = writer.insert_or_get(
        "theatre",
        {
            "idempotency_key": idempotency_key,
            "city_id": str(body.city_id),
            "name": body.name,
            "address": body.address,
        },
    )
    return row


@app.put("/admin/theatres/{theatre_id}")
def update_theatre(
    theatre_id: UUID,
    body: TheatreUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    _get_theatre_or_404(conn, theatre_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return _get_theatre_or_404(conn, theatre_id)

    set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
    fields["theatre_id"] = str(theatre_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE theatre SET {set_clause}, updated_at = now() WHERE id = %(theatre_id)s RETURNING *",
            fields,
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row)


@app.post("/admin/theatres/{theatre_id}/screens", status_code=201)
def create_screen(
    theatre_id: UUID,
    body: ScreenCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    _get_theatre_or_404(conn, theatre_id)
    idempotency_key = _derive_idempotency_key(theatre_id, body.name)
    writer = IdempotentWriter(conn)
    row, _created = writer.insert_or_get(
        "screen",
        {
            "idempotency_key": idempotency_key,
            "theatre_id": str(theatre_id),
            "name": body.name,
        },
    )
    return row


@app.put("/admin/screens/{screen_id}")
def update_screen(
    screen_id: UUID,
    body: ScreenUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM screen WHERE id = %s", (str(screen_id),))
        existing = cur.fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="screen not found")
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return dict(existing)

    set_clause = ", ".join(f"{col} = %({col})s" for col in fields)
    fields["screen_id"] = str(screen_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE screen SET {set_clause}, updated_at = now() WHERE id = %(screen_id)s RETURNING *",
            fields,
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row)


# --- seat layout authoring + draft lock (§4.5, §4.6, Appendix C) ---
# No row/column structure anywhere: every seat is an independent record
# (id, label, position_x/position_y, seat_type, price_multiplier). Draft
# creation deliberately has no idempotency key (confirmed with the user):
# a payload hash of screen_id+name isn't a safe dedup key here, since the
# same screen legitimately gets a brand-new draft on every re-edit cycle,
# often reusing the same name -- a dedup hit would silently hand back a
# stale, possibly-now-ACTIVE row instead of a fresh draft. Same shape of
# problem the design doc already flagged and deferred for BOOKING (§11.1).

_SEAT_FIELD_TO_COLUMN = {"x": "position_x", "y": "position_y"}


@app.post("/admin/seat-layouts/draft", status_code=201)
def create_seat_layout_draft(
    body: SeatLayoutDraftCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM screen WHERE id = %s", (str(body.screen_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="screen not found")

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO seat_layout (screen_id, name, status) VALUES (%s, %s, 'DRAFT') RETURNING *",
                (str(body.screen_id), body.name),
            )
            layout = dict(cur.fetchone())

            for seat in body.seats:
                cur.execute(
                    """
                    INSERT INTO seat_template
                        (id, seat_layout_id, label, position_x, position_y, seat_type, price_multiplier)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(seat.id),
                        layout["id"],
                        seat.label,
                        seat.x,
                        seat.y,
                        seat.seat_type,
                        seat.price_multiplier,
                    ),
                )
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=400, detail="duplicate seat id in request")

    conn.commit()
    layout["seats"] = _list_seats(conn, layout["id"])
    return layout


@app.post("/admin/seat-layouts/draft/{draft_id}/lock")
def acquire_seat_layout_lock(
    draft_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(_get_admin_identity),
) -> dict:
    """Acquire if free or stale, or heartbeat-refresh if the caller already
    holds it -- one atomic UPDATE so two concurrent acquire attempts can't
    both believe they won (§4.6)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE seat_layout
            SET locked_by_user_id = %(admin_id)s,
                lock_acquired_at = CASE
                    WHEN locked_by_user_id = %(admin_id)s THEN lock_acquired_at
                    ELSE now()
                END,
                lock_heartbeat_at = now()
            WHERE id = %(draft_id)s
              AND status = 'DRAFT'
              AND (
                    locked_by_user_id IS NULL
                    OR locked_by_user_id = %(admin_id)s
                    OR lock_heartbeat_at < now() - INTERVAL '1 minute' * %(stale_minutes)s
                  )
            RETURNING *
            """,
            {"admin_id": str(admin_id), "draft_id": str(draft_id), "stale_minutes": LOCK_STALE_MINUTES},
        )
        row = cur.fetchone()

    if row is None:
        conn.rollback()
        layout = _get_layout_or_404(conn, draft_id)
        if layout["status"] != "DRAFT":
            raise HTTPException(status_code=409, detail="layout is not in DRAFT status")
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "draft is locked by another admin",
                "locked_by_user_id": str(layout["locked_by_user_id"]),
                "lock_acquired_at": layout["lock_acquired_at"].isoformat(),
                "lock_heartbeat_at": layout["lock_heartbeat_at"].isoformat(),
            },
        )

    conn.commit()
    return dict(row)


@app.delete("/admin/seat-layouts/draft/{draft_id}/lock", status_code=204)
def release_seat_layout_lock(
    draft_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(_get_admin_identity),
) -> Response:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE seat_layout
            SET locked_by_user_id = NULL, lock_acquired_at = NULL, lock_heartbeat_at = NULL
            WHERE id = %s AND locked_by_user_id = %s
            RETURNING id
            """,
            (str(draft_id), str(admin_id)),
        )
        row = cur.fetchone()

    if row is None:
        conn.rollback()
        _get_layout_or_404(conn, draft_id)  # 404 if the draft itself doesn't exist
        raise HTTPException(status_code=409, detail="draft is not locked by you")

    conn.commit()
    return Response(status_code=204)


@app.patch("/admin/seat-layouts/draft/{draft_id}/seats/{seat_id}")
def update_seat(
    draft_id: UUID,
    seat_id: UUID,
    body: SeatPatch,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(_get_admin_identity),
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")

    params = {"seat_id": str(seat_id), "draft_id": str(draft_id), "admin_id": str(admin_id), "stale_minutes": LOCK_STALE_MINUTES}
    set_parts = []
    for field, value in fields.items():
        column = _SEAT_FIELD_TO_COLUMN.get(field, field)
        set_parts.append(f"{column} = %({column})s")
        params[column] = value

    # The EXISTS subquery re-checks lock ownership AND staleness fresh from
    # the DB as part of the very same statement that performs the edit --
    # this re-check can't be skipped or cached by accident (§4.6).
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE seat_template
            SET {", ".join(set_parts)}, updated_at = now()
            WHERE id = %(seat_id)s
              AND seat_layout_id = %(draft_id)s
              AND EXISTS (
                    SELECT 1 FROM seat_layout
                    WHERE id = %(draft_id)s
                      AND status = 'DRAFT'
                      AND locked_by_user_id = %(admin_id)s
                      AND lock_heartbeat_at >= now() - INTERVAL '1 minute' * %(stale_minutes)s
                  )
            RETURNING *
            """,
            params,
        )
        row = cur.fetchone()

    if row is None:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM seat_template WHERE id = %s AND seat_layout_id = %s",
                (str(seat_id), str(draft_id)),
            )
            seat_exists = cur.fetchone() is not None
        if not seat_exists:
            raise HTTPException(status_code=404, detail="seat not found")
        _raise_lock_error(conn, draft_id, admin_id)

    conn.commit()
    return dict(row)


@app.patch("/admin/seat-layouts/draft/{draft_id}/seats")
def bulk_update_seats(
    draft_id: UUID,
    body: BulkSeatPatch,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(_get_admin_identity),
) -> list[dict]:
    if not body.seat_ids:
        raise HTTPException(status_code=400, detail="seat_ids must not be empty")
    fields = body.model_dump(exclude_unset=True, exclude={"seat_ids"})
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")

    params = {
        "draft_id": str(draft_id),
        "admin_id": str(admin_id),
        "seat_ids": [str(s) for s in body.seat_ids],
        "stale_minutes": LOCK_STALE_MINUTES,
    }
    set_parts = []
    for field, value in fields.items():
        column = _SEAT_FIELD_TO_COLUMN.get(field, field)
        set_parts.append(f"{column} = %({column})s")
        params[column] = value

    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE seat_template
            SET {", ".join(set_parts)}, updated_at = now()
            WHERE seat_layout_id = %(draft_id)s
              AND id::text = ANY(%(seat_ids)s)
              AND EXISTS (
                    SELECT 1 FROM seat_layout
                    WHERE id = %(draft_id)s
                      AND status = 'DRAFT'
                      AND locked_by_user_id = %(admin_id)s
                      AND lock_heartbeat_at >= now() - INTERVAL '1 minute' * %(stale_minutes)s
                  )
            RETURNING *
            """,
            params,
        )
        rows = cur.fetchall()

    if not rows:
        conn.rollback()
        _raise_lock_error(conn, draft_id, admin_id)

    conn.commit()
    return [dict(r) for r in rows]


@app.post("/admin/seat-layouts/draft/{draft_id}/publish")
def publish_seat_layout(
    draft_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(_get_admin_identity),
) -> dict:
    """Flip to ACTIVE in the same UPDATE that re-checks the lock -- the
    screen assignment (screen_id) was already set at draft-creation time,
    so this one statement is the entire 'finalize + assign' transaction
    (§4.5): no window where one happened without the other."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE seat_layout
            SET status = 'ACTIVE',
                locked_by_user_id = NULL,
                lock_acquired_at = NULL,
                lock_heartbeat_at = NULL,
                updated_at = now()
            WHERE id = %(draft_id)s
              AND status = 'DRAFT'
              AND locked_by_user_id = %(admin_id)s
              AND lock_heartbeat_at >= now() - INTERVAL '1 minute' * %(stale_minutes)s
            RETURNING *
            """,
            {"draft_id": str(draft_id), "admin_id": str(admin_id), "stale_minutes": LOCK_STALE_MINUTES},
        )
        row = cur.fetchone()

    if row is None:
        conn.rollback()
        _raise_lock_error(conn, draft_id, admin_id)

    conn.commit()
    layout = dict(row)
    layout["seats"] = _list_seats(conn, draft_id)
    return layout


@app.post("/admin/seat-layouts/{layout_id}/clone", status_code=201)
def clone_seat_layout(
    layout_id: UUID,
    body: CloneRequest,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    source = _get_layout_or_404(conn, layout_id)
    if source["status"] != "ACTIVE":
        raise HTTPException(status_code=409, detail="only a published (ACTIVE) layout can be cloned")

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM screen WHERE id = %s", (str(body.target_screen_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="target screen not found")

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO seat_layout (screen_id, name, status) VALUES (%s, %s, 'DRAFT') RETURNING *",
            (str(body.target_screen_id), source["name"]),
        )
        new_layout = dict(cur.fetchone())

        # Omitting `id` lets the column default (gen_random_uuid()) mint a
        # fresh UUID per row -- labels/positions/types/active-status copy
        # verbatim (§4.5: "fresh UUIDs per seat, same labels/positions/types").
        cur.execute(
            """
            INSERT INTO seat_template
                (seat_layout_id, label, position_x, position_y, seat_type, price_multiplier, is_active)
            SELECT %s, label, position_x, position_y, seat_type, price_multiplier, is_active
            FROM seat_template
            WHERE seat_layout_id = %s
            RETURNING *
            """,
            (new_layout["id"], str(layout_id)),
        )
        new_seats = [dict(r) for r in cur.fetchall()]

    conn.commit()
    new_layout["seats"] = new_seats
    return new_layout
