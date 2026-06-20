CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- §4.1: theatre service is the source of truth for CITY. No admin
-- endpoint creates cities at this phase (absent from Appendix C) -- rows
-- come from the seed script directly.
CREATE TABLE city (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    state TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
