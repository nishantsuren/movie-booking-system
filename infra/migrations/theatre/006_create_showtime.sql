CREATE TABLE showtime (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE,
    movie_id UUID NOT NULL,
    screen_id UUID NOT NULL REFERENCES screen(id),
    start_time TIMESTAMPTZ NOT NULL,
    is_high_demand BOOLEAN NOT NULL DEFAULT false,
    base_price DOUBLE PRECISION NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_showtime_screen ON showtime(screen_id);
