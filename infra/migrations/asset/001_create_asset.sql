CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- §3.1, §4.1: local CDN mock's own metadata table, local-only, not part
-- of the production ownership model.
CREATE TABLE asset (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INT NOT NULL,
    storage_path TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
