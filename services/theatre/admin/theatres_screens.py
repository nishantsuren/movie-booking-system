"""Admin-only THEATRE + SCREEN CRUD (Appendix C)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from admin.schemas import ScreenCreate, ScreenUpdate, TheatreCreate, TheatreUpdate
from common.db import get_db
from common.idempotency import derive_idempotency_key
from common.theatres import get_theatre_or_404
from shared.auth.auth import AuthContext, require_role
from shared.idempotency.idempotency import IdempotentWriter

router = APIRouter(prefix="/admin")


@router.post("/theatres", status_code=201)
def create_theatre(
    body: TheatreCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM city WHERE id = %s", (str(body.city_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="city not found")

    idempotency_key = derive_idempotency_key(body.city_id, body.name)
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


@router.put("/theatres/{theatre_id}")
def update_theatre(
    theatre_id: UUID,
    body: TheatreUpdate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    get_theatre_or_404(conn, theatre_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_theatre_or_404(conn, theatre_id)

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


@router.get("/theatres/{theatre_id}/screens")
def list_screens_for_theatre(
    theatre_id: UUID,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> list[dict]:
    """Phase 9: there was no way to discover a theatre's existing screens
    at all -- only the create-response ever returned one."""
    get_theatre_or_404(conn, theatre_id)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM screen WHERE theatre_id = %s ORDER BY name", (str(theatre_id),))
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/theatres/{theatre_id}/screens", status_code=201)
def create_screen(
    theatre_id: UUID,
    body: ScreenCreate,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    get_theatre_or_404(conn, theatre_id)
    idempotency_key = derive_idempotency_key(theatre_id, body.name)
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


@router.put("/screens/{screen_id}")
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
