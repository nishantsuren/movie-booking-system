-- §4.4: per-city release window. city_id is a loose reference to theatre
-- service's CITY (§4.1) -- no DB-enforced FK across the service boundary.
CREATE TABLE movie_release (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    movie_id UUID NOT NULL REFERENCES movie(id),
    city_id UUID NOT NULL,
    release_date DATE NOT NULL,
    planned_end_date DATE,
    actual_end_date DATE,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_movie_release_city ON movie_release(city_id);
CREATE INDEX idx_movie_release_movie ON movie_release(movie_id);
