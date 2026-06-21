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

Phase 9 complete & verified: admin SPA (`apps/admin-web/`,
React+Vite+TypeScript, served under `/admin/` -- needs `base: '/admin/'`
in `vite.config.ts`, unlike customer-web at the root) -- CRUD for
movies/releases/theatres/screens/showtimes (Activate/Deactivate, not
Delete, per design), plus the seat-layout canvas editor (§4.5: line/
grid/curve/single-seat placement tools, multi-select + bulk edit) with
full draft-lock UX (§4.6: held/blocked-with-holder-identity/stale
banners, 25s heartbeat, explicit release button). Six admin list/get
endpoints were missing and had to be added first (catalog
`GET /admin/movies`+`/releases`; theatre `GET /admin/theatres/{id}/screens`,
`/admin/screens/{id}/seat-layouts`, `/admin/seat-layouts/{id}`,
`/admin/screens/{id}/showtimes`) -- design v17. Confirmed (not assumed)
Phase 8's CORS/SPA-fallback fixes already cover admin-web's own origin
and `/admin` path prefix; needed its own `assetsDir: 'app-assets'` fix
for the same Vite/`/assets`-route collision as customer-web. Two real
bugs caught by writing the E2E tests (both fixed, see design v17 and
git history, not repeated here): a UI race where create-forms could
submit with an empty FK before their dependent dropdown finished
loading (now disabled until ready, affects any future picker-backed
form); and the grid placement tool's row-labelling, which collapsed
every row to the same label. Two required E2E tests both pass for real:
cross-app integration (author a 152-seat layout via all four tools
through the canvas, publish, schedule+activate a showtime, confirm it
renders correctly in customer-web's seatmap, a different app/origin
entirely) and concurrent-edit (two real browser contexts, second
session blocked with the holder's identity shown, proceeds after either
explicit release or simulated staleness). Full backend regression still
48 passed/2 skipped; customer-web's own Phase-8 E2E suite (5 tests)
re-verified passing, unaffected by the shared-infra changes. Carries
forward from Phase 8: backend gap-filling pattern repeats here (an admin
UI structurally needs to list what it manages); draft creation still has
no idempotency key (same reasoning as before); lock-gated endpoints use
JWT `sub` when `AUTH_ENABLED=true` else `X-Admin-User-Id` (no real users
until Phase 10). Also still carried forward: sweep worker (Phase 6,
v13/v14); `RedisSeatLocker`/hash-tagged keys (v11); pre-existing Phase-2
gap (publish never deactivates prior ACTIVE layout); catalog's `?city=`
uses theatre's `city_id` directly (no local `CITY` copy till Phase 13).

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
