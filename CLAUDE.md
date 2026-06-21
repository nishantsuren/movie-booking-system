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

Phase 7 complete & verified: user service -- `app_user` table
(`"user"` is a reserved Postgres keyword, hence the name) with `role`
(CUSTOMER|ADMIN); `POST /auth/register` (bcrypt hash, email is the
natural idempotency key but a duplicate is a 409, not a silent
idempotent-replay -- password isn't part of the dedup key, so silently
returning the first registrant's row would be a real bug, not a
harmless retry); `POST /auth/login` issues a JWT via shared/auth/auth.py's
existing JWT_SECRET/JWT_ALGORITHM (no second auth scheme); `GET
/users/{id}`. `AUTH_ENABLED` stays `false` everywhere (Phase 10's job).
`tests/integration/test_phase7.py` (7 tests) + full regression across
all 7 phases (50 tests): 48 passed, 2 skipped (pre-existing self-skipping
fail-closed tests from Phases 3/5, unrelated to this phase, already
verified for real elsewhere) -- confirms nothing built so far
accidentally started requiring a token. Carries forward: sweep worker
(Phase 6, `services/booking/adapters/reconciliation_sweep.py`, v13/v14);
`RedisSeatLocker`/hash-tagged keys (v11); `SHOWTIME.base_price` (v10);
pre-existing Phase-2 gap (publish never deactivates prior ACTIVE
layout); draft creation has no idempotency key; lock-gated endpoints
use JWT `sub` when `AUTH_ENABLED=true` else `X-Admin-User-Id`; catalog's
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
