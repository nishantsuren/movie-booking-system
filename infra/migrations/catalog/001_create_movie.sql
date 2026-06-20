CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE movie (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    duration_minutes INT,
    language TEXT,
    poster_asset_id UUID,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
