-- v12: admin supplies movie_title alongside movie_id at showtime creation
-- (same trust tier as movie_id itself -- never live-validated against
-- catalog, §4.2). Default '' only backfills any pre-existing rows from
-- earlier phases' testing; application code always supplies a real value.
ALTER TABLE showtime ADD COLUMN movie_title TEXT NOT NULL DEFAULT '';
