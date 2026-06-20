-- booking_id is itself the idempotency key (§4.1: "PAYMENT.booking_id is
-- a loose reference, and the natural idempotency key") -- one payment per
-- booking, ever. No separate idempotency_key column needed.
CREATE TABLE payment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID NOT NULL UNIQUE,
    amount DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'SUCCESS',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
