CREATE TABLE booking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL,
    user_id UUID NOT NULL,
    showtime_id UUID NOT NULL,
    movie_title TEXT NOT NULL,
    seat_labels TEXT NOT NULL,
    price_paid DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CONFIRMED', 'EXPIRED', 'CANCELLED')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- v12 (§11.1): partial, not plain, unique -- the same (user, showtime,
-- seats) identity is legitimately recurring (a hold expires/is cancelled,
-- the same user retries the same seats later), so the key must free up
-- once a booking reaches a terminal state rather than blocking forever.
CREATE UNIQUE INDEX idx_booking_idempotency_key_live ON booking (idempotency_key)
    WHERE status IN ('PENDING', 'CONFIRMED');

CREATE INDEX idx_booking_showtime ON booking (showtime_id);

-- Real FK now that BOOKING exists (§4.1: "Real FK SHOWTIME_SEAT.booking_id
-- -> BOOKING.id once locked/booked"). Existing rows are NULL until a
-- booking locks them, which FK constraints permit.
ALTER TABLE showtime_seat ADD CONSTRAINT fk_showtime_seat_booking
    FOREIGN KEY (locked_by_booking_id) REFERENCES booking(id);
