"""Payment service — Phase 5.

PAYMENT (§4.1) -- mocked, always succeeds. booking_id is itself the
idempotency key (§11.1): one payment per booking, ever, no derived hash
needed since the natural key is already a single field.
"""
import os
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from db import get_db
from shared.idempotency.idempotency import IdempotentWriter

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

app = FastAPI(title="Payment service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "payment", "auth_enabled": AUTH_ENABLED}


class PaymentCreate(BaseModel):
    booking_id: UUID
    amount: float


@app.post("/payments", status_code=201)
def create_payment(
    body: PaymentCreate,
    conn=Depends(get_db),
) -> dict:
    """Mocked: always succeeds (Appendix A). booking_id is a loose
    reference (§4.1) -- never validated against booking service, same
    trust model as every other loose ref in this system."""
    writer = IdempotentWriter(conn)
    row, _created = writer.insert_or_get(
        "payment",
        {"booking_id": str(body.booking_id), "amount": body.amount, "status": "SUCCESS"},
        idempotency_key_column="booking_id",
    )
    return row


@app.get("/payments/{payment_id}")
def get_payment(payment_id: UUID, conn=Depends(get_db)) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM payment WHERE id = %s", (str(payment_id),))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return dict(row)
