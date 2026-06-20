"""Theatre service — Phase 1.

CITY, THEATRE, SCREEN (§4.1) -- not SEAT_LAYOUT/SEAT_TEMPLATE, that's
Phase 2. Customer browse + admin CRUD per Appendix A/C, plus
`GET /theatres?city=` filling a gap Appendix A leaves for real
city-scoped theatre discovery at this phase (showtimes, the more natural
discovery path, don't exist until Phase 3).
"""
import os
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from db import get_db
from shared.auth.auth import AuthContext, require_role
from shared.idempotency.idempotency import IdempotentWriter

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

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


def _require_idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    return key


def _get_theatre_or_404(conn, theatre_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM theatre WHERE id = %s", (str(theatre_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="theatre not found")
    return dict(row)


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
    request: Request,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    idempotency_key = _require_idempotency_key(request)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM city WHERE id = %s", (str(body.city_id),))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="city not found")

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
    request: Request,
    conn=Depends(get_db),
    _ctx: AuthContext = Depends(require_role("ADMIN")),
) -> dict:
    idempotency_key = _require_idempotency_key(request)
    _get_theatre_or_404(conn, theatre_id)
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
