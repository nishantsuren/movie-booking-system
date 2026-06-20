-- v12: a tiny local cache of showtime-level display data booking service
-- has no other way to know (movie_id -> title lives in catalog service).
-- Populated once per showtime by the materialize-seats call (§4.3), read
-- at BOOKING creation time to populate the movie_title snapshot field
-- with zero live cross-service calls on the booking hot path.
CREATE TABLE showtime_meta (
    showtime_id UUID PRIMARY KEY,
    movie_title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
