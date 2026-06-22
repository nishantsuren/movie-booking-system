# Movie ticket booking system — Claude Code project guide

Read every session. Update in-session when conventions change.

## Project
BookMyShow-style aggregator. Python/FastAPI, database-per-service.
**Read `docs/design.md` + `docs/implementation-plan.md` before every session.**

## Current state
**Next: Phase 9.5** — `TheatreIntegration` aggregator lock. See `docs/claude-code-workflow.md` Phase 9.5 prompt.

Phase 9 (admin SPA) complete. Design updated to **v18**: the system was missing the external seat lock against the theatre's ticketing system. Redis lock (§5.1) only protects within-platform; same seat is bookable via theatre's own site, other aggregators, box office. Fix: two-phase lock — (1) Redis (existing) then (2) `TheatreIntegration.hold_seats()` (§5.7, new). `MockTheatreIntegration` always succeeds → zero existing test changes. Only schema change: nullable `BOOKING.theatre_hold_id` (migration, NULL default, no backfill).

Open items carried forward:
- Draft creation has no idempotency key (unresolved)
- Admin lock endpoints: JWT `sub` if `AUTH_ENABLED=true`, else `X-Admin-User-Id`
- Phase-2 gap: publish doesn't deactivate prior ACTIVE layout on same screen
- Catalog `?city=` uses theatre's `city_id` directly (no local CITY copy until Phase 13)

**Update this section at session-end.**

## Conventions
- **DB**: `psycopg2` only, no ORM. Flag before switching.
- **Migrations**: `infra/migrations/NNN_*.sql`, applied by script, tracked via `schema_migrations`. No framework.
- **Idempotency**: `shared/idempotency/idempotency.py` — `INSERT...ON CONFLICT`, server-derived hash key, never client-supplied. See `_derive_idempotency_key` in catalog/theatre services. Design §11.1.
- **Auth**: `shared/auth/auth.py` — `get_auth_context`/`require_role(...)` on every endpoint. `AUTH_ENABLED` toggle (§3.2).
- **Events**: `shared/events/events.py` — `LoggingEventPublisher` no-op until Phase 13.
- **Testing**: DB tests hit real Dockerized Postgres, never mocked. Pattern: `shared/tests/`.
- **Services**: FastAPI app per service in `services/<name>/`. `docker-compose.yml` = Postgres + Redis only. Services run natively via `scripts/dev.sh`. Add new services there, not to compose.
- **TheatreIntegration**: `MockTheatreIntegration` always wired locally/in tests — never call a real theatre API locally. `THEATRE.integration_type` selects adapter at runtime. Design §5.7.
- **Frontend**: React+Vite+TypeScript in `apps/<name>/`. API URL via `VITE_API_BASE_URL`, never hardcoded. admin-web needs `base: '/admin/'` + `assetsDir: 'app-assets'` in vite.config.ts (`assets/` collides with CDN mock route). E2E: Playwright via `npm run test:e2e` against deployed build (`npm run build:deploy` → `local-cdn-mock/static/<name>/`), not dev server. `GET /admin` redirect to `/admin/` is explicit in local-cdn-mock — do not remove.

## Process
- One phase per session. Re-read scope + verification criteria first. Flag drift, don't act on it.
- Show actual test output before declaring a phase done.
- If `design.md` is wrong, say so and propose the fix — this has happened repeatedly and is expected.
- Commit after verification passes, message references the phase number.
