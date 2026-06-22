-- Phase 9.5 (design §5.7, v18): the external hold's opaque token from
-- the theatre's own ticketing system, set once hold_seats succeeds.
-- Needed later for confirm_hold/release_hold. NULL default, no
-- backfill -- pre-existing rows stay NULL, and §5.7's cancel/sweep
-- logic explicitly skips the release_hold call when this is NULL
-- (those bookings predate the external lock entirely).
ALTER TABLE booking ADD COLUMN theatre_hold_id TEXT;
