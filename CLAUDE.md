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

Phase 8 complete & verified: customer SPA (`apps/customer-web/`,
React+Vite+TypeScript, confirmed with user) -- browse/showtimes/seatmap/
booking/mocked-payment/confirmation, calling only through the routing
service, bundle built and deployed into `local-cdn-mock/static/customer/`
(`npm run build:deploy`), never served from the Vite dev server for
testing. `VITE_API_BASE_URL` env-configured (§3.2), never hardcoded.
Backend gap filled first (frontend structurally needed it): `GET
/cities`, `GET /showtimes/{id}` (theatre, plain), enriched `GET
/movies/{id}/showtimes?city=&date=` (theatre, one live catalog call/request)
and `GET /showtimes/{id}/seatmap` (booking, wrapped with movie/theatre/
screen/time/price from `SHOWTIME_META`, extended at materialize time --
v16, see design.md). Three real infra bugs a browser caught that curl
couldn't: routing had no CORS headers (browser silently blocks
cross-origin responses; curl never enforces CORS); local-cdn-mock's
`StaticFiles` had no SPA fallback (client-side routes 404'd on direct
nav); Vite's default `assets/` build dir collided with the existing
`GET /assets/{asset_id}` route (422s parsing bundle filenames as UUIDs)
-- fixed via `vite.config.ts`'s `assetsDir`, not the API contract.
Countdown UX (confirmed w/ user, conditional on the no-double-booking
guarantee already proven in Phases 4/6): communicates the post-v14
grace window explicitly rather than asserting a hard deadline.
Playwright E2E suite (`apps/customer-web/e2e/`, chosen over Cypress for
native multi-context support, needed for the seat-conflict test): happy
path, seat-conflict (two real browser contexts, found and fixed a test
bug -- `waitForURL` trivially matches a URL the page is already on, use
`waitForResponse` for race assertions instead), payment failure (route
interception -- backend failure already proven for real in
`test_phase5.py`, this tests the frontend's rendering), and both
countdown-grace-window outcomes (one waits ~29s for the real live sweep
worker). All 5 pass; full backend regression (50 tests) still 48
passed/2 skipped, unaffected. Carries forward: sweep worker (Phase 6,
v13/v14); `RedisSeatLocker`/hash-tagged keys (v11); `SHOWTIME.base_price`
(v10); pre-existing Phase-2 gap (publish never deactivates prior ACTIVE
layout); draft creation has no idempotency key; lock-gated endpoints use
JWT `sub` when `AUTH_ENABLED=true` else `X-Admin-User-Id`; catalog's
`?city=` uses theatre's `city_id` directly (no local `CITY` copy till
Phase 13).

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
- **Frontend** (Phase 8+): React+Vite+TypeScript, in `apps/<name>/`. API
  base URL via `import.meta.env.VITE_API_BASE_URL` (`.env`), never
  hardcoded (§3.2). E2E: Playwright in `apps/<name>/e2e/`, run via
  `npm run test:e2e` against the build deployed into
  `local-cdn-mock/static/<name>/` (`npm run build:deploy`) — not the Vite
  dev server, so tests exercise the actual serving path (and its real
  gotchas: CORS on routing, SPA fallback on local-cdn-mock, the
  `/assets` build-dir collision — see design.md v16 before re-deriving
  any of these).

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
