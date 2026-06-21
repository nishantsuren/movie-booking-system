-- Phase 8: the seatmap page needs theatre/screen/time/price context
-- alongside the seats themselves. Cached here at materialize time (same
-- pattern as movie_title, v12) rather than fetched live from theatre on
-- every seatmap read -- seatmap views are this system's highest-volume
-- read path (§2: ~4M/day vs ~250K bookings/day), so a live cross-service
-- call here would be the wrong place to pay that cost.
ALTER TABLE showtime_meta
    ADD COLUMN theatre_name TEXT NOT NULL DEFAULT '',
    ADD COLUMN screen_name TEXT NOT NULL DEFAULT '',
    ADD COLUMN start_time TIMESTAMPTZ,
    ADD COLUMN base_price DOUBLE PRECISION;
