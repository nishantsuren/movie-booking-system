"""Local CDN mock — Phase 1.

Two internally separate route groups deployed as one process (§3.1):
SPA bundle static-file serving (`/`, `/admin/`) and the asset
upload/serve API (`/assets`), backed by the ASSET metadata table.
Local-only -- not part of the production ownership model (§4.1) -- so,
per this phase's explicit scope, no AUTH_ENABLED gating here (unlike
catalog/theatre's admin endpoints).
"""
import os
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db import get_db
from shared.idempotency.idempotency import IdempotentWriter

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage" / "assets"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local CDN mock")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "local-cdn-mock", "auth_enabled": AUTH_ENABLED}


def _require_idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    return key


# --- asset route group (§3.1, §4.1: ASSET metadata table) ---

@app.post("/assets", status_code=201)
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    conn=Depends(get_db),
) -> dict:
    """Admin-facing upload path (Appendix C), used e.g. for movie posters."""
    idempotency_key = _require_idempotency_key(request)
    asset_id = uuid.uuid4()
    contents = await file.read()
    dest = STORAGE_DIR / f"{asset_id}_{file.filename}"
    dest.write_bytes(contents)

    writer = IdempotentWriter(conn)
    row, created = writer.insert_or_get(
        "asset",
        {
            "id": str(asset_id),
            "idempotency_key": idempotency_key,
            "filename": file.filename,
            "content_type": file.content_type or "application/octet-stream",
            "byte_size": len(contents),
            "storage_path": str(dest),
        },
    )
    if not created:
        # Replay of an already-stored upload -- discard the bytes we just
        # wrote under a fresh id; the original asset is the source of truth.
        dest.unlink(missing_ok=True)
    return row


@app.get("/assets/{asset_id}")
def get_asset(asset_id: UUID, conn=Depends(get_db)) -> FileResponse:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM asset WHERE id = %s", (str(asset_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="asset not found")
    path = Path(row["storage_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="asset file missing from storage")
    return FileResponse(path, media_type=row["content_type"], filename=row["filename"])


# --- SPA bundle static-file route group (§3.1) ---
# Registered last: Starlette matches routes in registration order, and a
# root-mounted StaticFiles app would otherwise shadow every route above.

for spa_name in ("customer", "admin"):
    spa_dir = BASE_DIR / "static" / spa_name
    spa_dir.mkdir(parents=True, exist_ok=True)

app.mount("/admin", StaticFiles(directory=BASE_DIR / "static" / "admin", html=True), name="admin-spa")
app.mount("/", StaticFiles(directory=BASE_DIR / "static" / "customer", html=True), name="customer-spa")
