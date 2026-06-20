# Movie ticket booking system — Claude Code project guide

Read automatically every session. Keep current — update in the same
session conventions change, not as an afterthought.

## Project

BookMyShow-style booking platform: Python/FastAPI microservices,
database-per-service. Architecture/rationale: `docs/design.md`. Phased
build order + verification criteria: `docs/implementation-plan.md`.
**Read both every session** before starting work — don't rely on
conversation memory.

## Current state

Phase 5 complete & verified: `BOOKING` + full saga (select seats ->
PENDING -> mocked payment -> confirm -> BOOKED) via `BookingOrchestrator`
(`services/booking/{domain,application,adapters}/`); payment service
built out for real. Design v12 fixes (see design.md changelog): booking
idempotency key = hash(showtime_id+user_id+sorted seat_ids) via a
**partial** unique index (`WHERE status IN (PENDING,CONFIRMED)`, not
plain -- same triple is a legitimate recurring identity); `movie_title`
has no other path into BOOKING's snapshot, so admin now supplies it at
showtime-creation (same trust tier as movie_id), passed through
materialize-seats into a local `SHOWTIME_META` cache -- zero live
cross-service calls on the booking hot path. `confirm`'s sole
concurrency guard is the SHOWTIME_SEAT PK-scoped conditional update
(§5.6), not a separate booking-row lock. `tests/integration/test_phase5.py`
(8 tests, 1 verified for real w/ payment stopped) + full regression (38
tests) pass. Showtime-deletion-race test dropped per v10 (no row removal
to race against). Carries forward: `RedisSeatLocker`/hash-tagged keys
(Phase 4, v11); `SHOWTIME.base_price` (Phase 3, v10); pre-existing
Phase-2 gap (publish never deactivates a screen's prior ACTIVE layout,
still unfixed); draft creation has no idempotency key; lock-gated
endpoints use JWT `sub` when `AUTH_ENABLED=true` else `X-Admin-User-Id`
(no real users until Phase 7); catalog's `?city=` uses theatre's
`city_id` directly (no local `CITY` copy till Phase 13).

**Update this section at session-end** with the now-completed phase.

## Conventions (Phase 0) — reuse, don't reinvent

- **DB access**: plain `psycopg2`, no ORM. Flag explicitly before switching.
- **Migrations**: numbered SQL files (`infra/migrations/001_*.sql`),
  applied in order by a script, tracked via per-DB `schema_migrations`
  table. No framework.
- **Idempotency**: `shared/idempotency/idempotency.py`
  (`INSERT...ON CONFLICT`, design §11.1). Use for every new
  resource-creating endpoint. Key is derived server-side from the
  entity's identity-defining fields (a deterministic hash) — never a
  client-supplied header. See `_derive_idempotency_key` in
  `services/catalog/main.py` / `services/theatre/main.py`.
- **Auth**: `shared/auth/auth.py` — `AUTH_ENABLED` toggle (§3.2),
  `get_auth_context`/`require_role(...)` deps. Use on every new endpoint.
- **Events**: `shared/events/events.py` — `EventPublisher` interface;
  `LoggingEventPublisher` no-op until Phase 13.
- **Testing**: DB-touching tests run against real Dockerized Postgres,
  never mocked (`shared/tests/` pattern) — critical for concurrency
  phases (4, 5, 6).
- **Service shape**: each backend service = own FastAPI app in
  `services/<name>/`, own Dockerfile + requirements.txt (for later
  containerized deployment, §15). `docker-compose.yml` is **Postgres +
  Redis only** — services run natively on the host via `scripts/dev.sh`
  for fast iteration (no image rebuild per change). Don't add services
  to `docker-compose.yml`; add new ones to `scripts/dev.sh`'s
  `start_service` calls, following the existing env-var pattern
  (`DATABASE_URL` built from `.env`'s Postgres settings, pointed at the
  service's own logical database).

## Working process

- **One phase per session**: state the phase, re-read its scope/verification
  in the implementation plan, stay inside it — flag drift temptation
  instead of acting on it.
- **Run the phase's specified verification tests**, don't invent new
  ones. Show actual output before declaring done.
- **If design.md is wrong/impractical, say so + propose the fix**
  (has happened before, e.g. seat-uniqueness index) — that's the process
  working, not a problem to hide.
- **Commit at end of each phase** (after verification passes), message
  references the phase number.
