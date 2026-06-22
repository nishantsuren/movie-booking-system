-- Phase 9.5 (design §5.7/§13, v18): the Outbox for confirm_hold/
-- release_hold retries. A row is written in the *same transaction* as
-- the booking confirm (CONFIRM_HOLD) or the booking cancel/sweep-expiry
-- (RELEASE_HOLD) -- a separate relay process (theatre_outbox_relay.py,
-- same structural pattern as reconciliation_sweep.py) then calls the
-- theatre API independently with bounded backoff, so neither the
-- confirm/cancel hot path nor the sweep ever blocks on theatre API
-- latency or its own retries.
CREATE TABLE pending_theatre_call (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_type TEXT NOT NULL CHECK (call_type IN ('CONFIRM_HOLD', 'RELEASE_HOLD')),
    booking_id UUID NOT NULL,
    theatre_hold_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- §13: "if retries are exhausted, flag for manual reconciliation --
    -- an ops concern, not a data-integrity one". last_error is what an
    -- operator would actually look at for that; FAILED (not a fourth
    -- retry-forever loop) is what stops the relay from spinning on it.
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The relay's own due-work query (status = 'PENDING' AND next_attempt_at
-- <= now()) -- partial, since DONE/FAILED rows are never queried again.
CREATE INDEX idx_pending_theatre_call_due ON pending_theatre_call (next_attempt_at) WHERE status = 'PENDING';

CREATE INDEX idx_pending_theatre_call_booking ON pending_theatre_call (booking_id);
