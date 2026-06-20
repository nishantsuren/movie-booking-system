CREATE TABLE seat_template (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seat_layout_id UUID NOT NULL REFERENCES seat_layout(id),
    label TEXT NOT NULL,
    position_x DOUBLE PRECISION NOT NULL,
    position_y DOUBLE PRECISION NOT NULL,
    seat_type TEXT NOT NULL,
    price_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_seat_template_layout ON seat_template(seat_layout_id);
