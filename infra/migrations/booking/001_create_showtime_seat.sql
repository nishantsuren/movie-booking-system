CREATE TABLE showtime_seat (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    showtime_id UUID NOT NULL,
    seat_template_id UUID NOT NULL,
    label TEXT NOT NULL,
    position_x DOUBLE PRECISION NOT NULL,
    position_y DOUBLE PRECISION NOT NULL,
    seat_type TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE', 'LOCKED', 'BOOKED')),
    locked_by_booking_id UUID,
    lock_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Guards against duplicate materialization (e.g. a retried materialize
    -- call, design §5.3 point 2) -- unconditional, not scoped to status and
    -- not keyed on the editable display label. A different failure mode
    -- than the booking race itself (Phase 5's PK-scoped conditional update).
    UNIQUE (showtime_id, seat_template_id)
);

CREATE INDEX idx_showtime_seat_showtime ON showtime_seat(showtime_id);
