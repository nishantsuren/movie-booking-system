"""THEATRE lookups shared by both customer and admin routes -- the
not-found check is identical regardless of which side is asking.
"""
from uuid import UUID

from fastapi import HTTPException


def get_theatre_or_404(conn, theatre_id: UUID) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM theatre WHERE id = %s", (str(theatre_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="theatre not found")
    return dict(row)
