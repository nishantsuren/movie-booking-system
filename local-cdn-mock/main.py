"""Local CDN mock — Phase 1.

Two internally separate route groups deployed as one process (§3.1):
SPA bundle static-file serving (`/`, `/admin/`) and the asset
upload/serve API (`/assets`), backed by the ASSET metadata table.
Local-only -- not part of the production ownership model (§4.1) -- so,
per this phase's explicit scope, no AUTH_ENABLED gating here (unlike
catalog/theatre's admin endpoints).
"""
import hashlib
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from config import AUTH_ENABLED, BASE_DIR, STORAGE_DIR
from db import get_db
from shared.idempotency.idempotency import IdempotentWriter

STORAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local CDN mock")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "local-cdn-mock", "auth_enabled": AUTH_ENABLED}


# --- asset route group (§3.1, §4.1: ASSET metadata table) ---

@app.post("/assets", status_code=201)
async def upload_asset(
    file: UploadFile = File(...),
    conn=Depends(get_db),
) -> dict:
    """Admin-facing upload path (Appendix C), used e.g. for movie posters.

    Idempotency key is the content hash of the uploaded bytes (§11.1) --
    content-addressable, no client-managed header. Re-uploading identical
    bytes (e.g. a retried upload) always returns the original asset;
    uploading the same bytes under a different filename also dedupes onto
    the original, a deliberate trade-off of using content as the only
    identity signal for a blob store.
    """
    asset_id = uuid.uuid4()
    contents = await file.read()
    idempotency_key = hashlib.sha256(contents).hexdigest()
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


class SPAStaticFiles(StaticFiles):
    """Plain StaticFiles 404s on any path that isn't a real file (e.g.
    /movies/abc/showtimes, a client-side react-router route) -- there is
    no server-side route for it, only the SPA's own JS knows what to do
    with it. Falls back to index.html (status 200, not a 404.html) for
    any unmatched path, the standard SPA-hosting pattern (Phase 8 found
    this the hard way: a direct navigation/page reload on any non-root
    customer-web route 404'd until this existed)."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


for spa_name in ("customer", "admin"):
    spa_dir = BASE_DIR / "static" / spa_name
    spa_dir.mkdir(parents=True, exist_ok=True)

@app.get("/admin")
def admin_trailing_slash_redirect() -> RedirectResponse:
    """Starlette's Mount("/admin", ...) below only matches "/admin/...";
    a bare "/admin" (no trailing slash) falls all the way through to the
    "/" mount instead and silently serves customer-web's bundle (found
    by a real user hitting exactly this URL). Must be registered before
    the "/" mount, since routes are matched in registration order."""
    return RedirectResponse(url="/admin/")


app.mount("/admin", SPAStaticFiles(directory=BASE_DIR / "static" / "admin", html=True), name="admin-spa")
app.mount("/", SPAStaticFiles(directory=BASE_DIR / "static" / "customer", html=True), name="customer-spa")
