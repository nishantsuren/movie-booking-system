"""Theatre service — Phase 1-3.

CITY, THEATRE, SCREEN (§4.1, Phase 1); SEAT_LAYOUT/SEAT_TEMPLATE + draft
lock (§4.5/§4.6, Phase 2); SHOWTIME + seat materialization (§4.3, Phase 3).
Customer browse + admin CRUD per Appendix A/C, plus `GET /theatres?city=`
filling a gap Appendix A leaves for real city-scoped theatre discovery.

This module is a thin composition root: it creates the FastAPI app,
exposes /health, and wires in the customer/admin routers. Customer-
facing route handlers live in customer/routes.py. Admin-facing ones are
split across three modules by sub-domain (admin/theatres_screens.py,
admin/seat_layouts.py, admin/showtimes.py) plus admin/schemas.py for
their request bodies and admin/lock.py for the seat-layout draft-lock
helpers shared between them -- theatre has no customer-facing writes at
all, so every Pydantic body and every mutating endpoint in this service
belongs to the admin side. Code genuinely shared between admin and
customer (DB dependency, idempotency-key derivation, THEATRE lookups,
AUTH_ENABLED) lives in common/.
"""
from fastapi import FastAPI

from admin.seat_layouts import router as admin_seat_layouts_router
from admin.showtimes import router as admin_showtimes_router
from admin.theatres_screens import router as admin_theatres_screens_router
from common.config import AUTH_ENABLED
from customer.routes import router as customer_router

app = FastAPI(title="Theatre service")
app.include_router(customer_router)
app.include_router(admin_theatres_screens_router)
app.include_router(admin_seat_layouts_router)
app.include_router(admin_showtimes_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "theatre", "auth_enabled": AUTH_ENABLED}
