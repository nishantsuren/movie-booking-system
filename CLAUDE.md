# Movie ticket booking system — Claude Code project guide

Read every session. Update in-session when conventions change.

## Project
BookMyShow-style aggregator. Python/FastAPI, database-per-service.
**Read `docs/design.md` + `docs/implementation-plan.md` before every session.**

## Current state
**Next: Phase 10** — auth hardening (`AUTH_ENABLED=true` everywhere + full role×endpoint access-control matrix, §3.2/§15).

Phase 9.5 complete: `TheatreIntegration` (§5.7, v18) — the external/aggregator lock, since Redis (§5.1) only protects within-platform and the same seat is bookable via the theatre's own site, other aggregators, box office. Two-phase lock in `BookingOrchestrator.select_seats`: (1) Redis (existing), (2) `theatre.hold_seats()` (new) — conflict or timeout compensates by releasing the Redis lock. `MockTheatreIntegration` always succeeds by default → zero changes needed to any pre-existing test; its `hold_mode`/`confirm_hold_should_fail`/`release_hold_should_fail` knobs (env-var-driven for the live process, direct-construction for tests) exist purely to drive the new failure-path tests, mirroring how earlier phases stopped a real service process to simulate a downstream outage — there's no real theatre API locally to fail on demand otherwise. `confirm_hold`/`release_hold` go through a real Outbox (new `pending_theatre_call` table + `theatre_outbox_relay.py`, same N-replicas-one-active pattern as `reconciliation_sweep.py`) rather than synchronously — built for real, not deferred, since the failure tests need genuine retry behavior to verify. Sweep worker's expiry path also enqueues `RELEASE_HOLD`, not just the cancel endpoint. New `theatre_availability_sync.py` job (shadow-inventory reconciliation, §5.7) — a third worker on the same pattern. Schema: nullable `BOOKING.theatre_hold_id` (no backfill, skipped cleanly on confirm/cancel when null). `CircuitBreaker` extracted from `payment_client.py` into its own module (generic `trips_on` param) and reused for the theatre integration — `payment_client.py` keeps a thin subclass defaulting `trips_on=PaymentServiceUnavailable` so the pre-existing breaker test needed zero changes. Per-theatre adapter selection (`THEATRE.integration_type` choosing the concrete adapter at runtime) is *not* built — out of this phase's bounded scope (interface/mock/wiring/failure-tests only) and still real future work, §16.8.

Open items carried forward:
- Draft creation has no idempotency key (unresolved)
- Admin lock endpoints: JWT `sub` if `AUTH_ENABLED=true`, else `X-Admin-User-Id`
- Phase-2 gap: publish doesn't deactivate prior ACTIVE layout on same screen
- Catalog `?city=` uses theatre's `city_id` directly (no local CITY copy until Phase 13)
- Real `TheatreIntegration` adapters (Vista, Moviebook, etc.) and `THEATRE.integration_type`-based runtime selection — §16.8, production work

**Update this section at session-end.**

## Conventions
- **DB**: `psycopg2` only, no ORM. Flag before switching.
- **Migrations**: `infra/migrations/NNN_*.sql`, applied by script, tracked via `schema_migrations`. No framework.
- **Idempotency**: `shared/idempotency/idempotency.py` — `INSERT...ON CONFLICT`, server-derived hash key, never client-supplied. See `_derive_idempotency_key` in catalog/theatre services. Design §11.1.
- **Auth**: `shared/auth/auth.py` — `get_auth_context`/`require_role(...)` on every endpoint. `AUTH_ENABLED` toggle (§3.2).
- **Events**: `shared/events/events.py` — `LoggingEventPublisher` no-op until Phase 13.
- **Testing**: DB tests hit real Dockerized Postgres, never mocked. Pattern: `shared/tests/`.
- **Services**: FastAPI app per service in `services/<name>/`. `docker-compose.yml` = Postgres + Redis only. Services run natively via `scripts/dev.sh`. Add new services there, not to compose. Standalone workers (sweep/relay/sync style) that import sibling modules (`adapters.X`, `domain.X`) must be started via `python -m adapters.module_name`, not a file path — `python path/to/file.py` sets `sys.path[0]` to that file's own directory, breaking the self-import; `-m` sets it to cwd instead. `start_worker` in `scripts/dev.sh` already does this.
- **TheatreIntegration**: `MockTheatreIntegration` always wired locally/in tests — never call a real theatre API locally. Design §5.7. Per-theatre adapter selection (`THEATRE.integration_type`) is future work, §16.8 — not built yet.
- **Frontend**: React+Vite+TypeScript in `apps/<name>/`. API URL via `VITE_API_BASE_URL`, never hardcoded. admin-web needs `base: '/admin/'` + `assetsDir: 'app-assets'` in vite.config.ts (`assets/` collides with CDN mock route). E2E: Playwright via `npm run test:e2e` against deployed build (`npm run build:deploy` → `local-cdn-mock/static/<name>/`), not dev server. `GET /admin` redirect to `/admin/` is explicit in local-cdn-mock — do not remove.

## Process
- One phase per session. Re-read scope + verification criteria first. Flag drift, don't act on it.
- Show actual test output before declaring a phase done.
- If `design.md` is wrong, say so and propose the fix — this has happened repeatedly and is expected.
- Commit after verification passes, message references the phase number.
