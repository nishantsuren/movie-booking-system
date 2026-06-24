"""Seat-layout draft lock (§4.6) -- admin-only concern (no customer-
facing seat-layout endpoints exist at all), shared by admin/seat_layouts.py's
own handlers. Kept as its own module since `_get_admin_identity` in
particular is a FastAPI dependency several handlers there inject.
"""
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from shared.auth.auth import AuthContext, get_auth_context

from common.config import AUTH_ENABLED, LOCK_STALE_MINUTES


def get_admin_identity(request: Request, ctx: AuthContext = Depends(get_auth_context)) -> UUID:
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


def get_layout_or_404(conn, layout_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM seat_layout WHERE id = %s", (str(layout_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="seat layout not found")
    return dict(row)


def list_seats(conn, layout_id: UUID) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM seat_template WHERE seat_layout_id = %s ORDER BY created_at",
            (str(layout_id),),
        )
        return [dict(r) for r in cur.fetchall()]


def raise_lock_error(conn, draft_id: UUID, admin_id: UUID) -> None:
    """Called after a lock-gated mutation affects zero rows, to turn that
    into the right error. Re-reads current state fresh -- this function
    itself never assumes the lock is still held, since by construction the
    caller only reaches here after the gated SQL already determined it isn't."""
    layout = get_layout_or_404(conn, draft_id)
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
