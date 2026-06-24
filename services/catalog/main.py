"""Catalog service — Phase 1.

MOVIE + MOVIE_RELEASE (§4.4), customer browse (Appendix A) and admin CRUD
(Appendix C). Soft-delete is genuine (`is_active`, §4.2) — never
cascades, never hard-deletes.

This module is a thin composition root: it creates the FastAPI app,
exposes /health, and wires in the customer/admin routers. Customer-
facing route handlers live in customer/routes.py, admin-facing ones in
admin/routes.py + admin/schemas.py, and code genuinely shared between
the two (DB dependency, idempotency-key derivation, MOVIE lookups) in
common/ -- see this service's README/CLAUDE.md note on why the two
audiences are kept in separate modules.
"""
from fastapi import FastAPI

from admin.routes import router as admin_router
from common.config import AUTH_ENABLED
from customer.routes import router as customer_router

app = FastAPI(title="Catalog service")
app.include_router(customer_router)
app.include_router(admin_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "catalog", "auth_enabled": AUTH_ENABLED}
