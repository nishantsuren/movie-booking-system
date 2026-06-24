"""Admin-only seat-layout authoring + draft lock (§4.5, §4.6, Appendix
C). No row/column structure anywhere: every seat is an independent
record (id, label, position_x/position_y, seat_type, price_multiplier).
Draft creation deliberately has no idempotency key (confirmed with the
user): a payload hash of screen_id+name isn't a safe dedup key here,
since the same screen legitimately gets a brand-new draft on every
re-edit cycle, often reusing the same name -- a dedup hit would
silently hand back a stale, possibly-now-ACTIVE row instead of a fresh
draft. Same shape of problem the design doc already flagged and
deferred for BOOKING (§11.1).
"""
from uuid import UUID

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Response

from admin.lock import get_admin_identity, get_layout_or_404, list_seats, raise_lock_error
from common.config import LOCK_STALE_MINUTES
from admin.schemas import BulkSeatPatch, CloneRequest, SeatLayoutDraftCreate, SeatPatch
from common.db import get_db
from shared.auth.auth import AuthContext, require_role

router = APIRouter(prefix="/admin")

_SEAT_FIELD_TO_COLUMN = {"x": "position_x", "y": "position_y"}


@router.get("/screens/{screen_id}/seat-layouts")
def list_seat_layouts_for_screen(
    screen_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> list[dict]:
    """Phase 9: finding an existing draft to resume editing, or the
    published ACTIVE layout to clone, previously had no path besides
    remembering the layout_id from whenever it was created."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM screen WHERE id = %s", (str(screen_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="screen not found")
        cur.execute(
            "SELECT * FROM seat_layout WHERE screen_id = %s ORDER BY created_at DESC",
            (str(screen_id),),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/seat-layouts/{layout_id}")
def get_seat_layout(
    layout_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    """Phase 9: standalone read, including current lock status -- needed
    to resume an in-progress draft (e.g. after a page reload) and to show
    "currently locked by X" before even attempting to acquire the lock,
    not just as a side effect of create/lock/publish/clone responses."""
    layout = get_layout_or_404(conn, layout_id)
    layout["seats"] = list_seats(conn, layout_id)
    return layout


@router.post("/seat-layouts/draft", status_code=201)
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
    layout["seats"] = list_seats(conn, layout["id"])
    return layout


@router.post("/seat-layouts/draft/{draft_id}/lock")
def acquire_seat_layout_lock(
    draft_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(get_admin_identity),
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
        layout = get_layout_or_404(conn, draft_id)
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


@router.delete("/seat-layouts/draft/{draft_id}/lock", status_code=204)
def release_seat_layout_lock(
    draft_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(get_admin_identity),
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
        get_layout_or_404(conn, draft_id)  # 404 if the draft itself doesn't exist
        raise HTTPException(status_code=409, detail="draft is not locked by you")

    conn.commit()
    return Response(status_code=204)


@router.patch("/seat-layouts/draft/{draft_id}/seats/{seat_id}")
def update_seat(
    draft_id: UUID,
    seat_id: UUID,
    body: SeatPatch,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(get_admin_identity),
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
        raise_lock_error(conn, draft_id, admin_id)

    conn.commit()
    return dict(row)


@router.patch("/seat-layouts/draft/{draft_id}/seats")
def bulk_update_seats(
    draft_id: UUID,
    body: BulkSeatPatch,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(get_admin_identity),
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
        raise_lock_error(conn, draft_id, admin_id)

    conn.commit()
    return [dict(r) for r in rows]


@router.post("/seat-layouts/draft/{draft_id}/publish")
def publish_seat_layout(
    draft_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
    admin_id: UUID = Depends(get_admin_identity),
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
        raise_lock_error(conn, draft_id, admin_id)

    conn.commit()
    layout = dict(row)
    layout["seats"] = list_seats(conn, draft_id)
    return layout


@router.post("/seat-layouts/{layout_id}/clone", status_code=201)
def clone_seat_layout(
    layout_id: UUID,
    body: CloneRequest,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    source = get_layout_or_404(conn, layout_id)
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
