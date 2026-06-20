# Movie ticket booking system — Claude Code project guide

This file is read automatically at the start of every Claude Code session
in this repo. Keep it current — if conventions change, update this file
in the same session, not as an afterthought.

## What this project is

A BookMyShow-style movie ticket booking platform, built as Python/FastAPI
microservices with database-per-service. Full architecture, rationale,
and every design tradeoff made along the way: `docs/design.md`. Phased
build order with per-phase scope and verification criteria:
`docs/implementation-plan.md`. **Read both before starting any work in
this repo, every session** — don't rely on conversation memory.

## Current state

Phase 0 (foundation scaffolding) is complete and verified: Docker Compose
stack (Postgres with six logical databases, Redis, thin FastAPI stub
services, a working routing service), plus `shared/idempotency` and
`shared/auth` with real tests passing against actual Postgres.

**Update this section at the end of every session** with which phase is
now complete, so the next session — yours or a human's — knows where
things stand without re-deriving it from git history.

## Conventions established in Phase 0 — reuse these, don't reinvent them

- **Database access**: plain `psycopg2`, not an ORM. Stay consistent
  unless there's a real reason to switch — raise it explicitly if you
  think there is, don't switch silently mid-project.
- **Migrations**: numbered SQL files (e.g. `infra/migrations/001_*.sql`)
  applied in order by a small script, tracked via a `schema_migrations`
  table per database. No migration framework — keep it as plain as the
  rest of the data-access layer.
- **Idempotency**: `shared/idempotency/idempotency.py` implements the
  `INSERT ... ON CONFLICT` pattern from design doc §11.1. Every new
  mutating endpoint that creates a resource should use this, not a
  parallel mechanism.
- **Auth**: `shared/auth/auth.py` implements the `AUTH_ENABLED` toggle
  (§3.2) — `get_auth_context` and `require_role(...)` as FastAPI
  dependencies. Every new endpoint, customer or admin, should use these.
- **Events**: `shared/events/events.py` — `EventPublisher` interface,
  `LoggingEventPublisher` as the no-op implementation until Phase 13.
- **Testing**: anything touching the database is tested against the real
  Dockerized Postgres, never mocked — see `shared/tests/` for the
  established pattern. This matters most for the concurrency-sensitive
  phases (4, 5, 6) where a mock would hide the exact bugs the tests exist
  to catch.
- **Service shape**: each backend service is its own FastAPI app under
  `services/<name>/`, with its own `Dockerfile` and `requirements.txt`,
  registered in `docker-compose.yml`.

## Working process

- **One implementation-plan phase per session.** State which phase at
  the start, re-read its scope and verification criteria in
  `docs/implementation-plan.md`, and stay inside that boundary — don't
  drift into a later phase's endpoints or features even when related work
  makes it tempting. Flag the temptation instead of acting on it.
- **Write and run the tests specified in that phase's verification
  criteria** — they're already defined in the implementation plan, don't
  invent a different test plan. Show the actual test output before
  declaring a phase done, not just a description of what the tests would
  check.
- **If `docs/design.md` turns out to be wrong, incomplete, or impractical
  once you're actually building it, say so explicitly and propose the
  specific edit.** This has already happened several times during design
  (the seat-uniqueness index was wrong in an early revision and got
  corrected after implementation-level scrutiny) — expect the same during
  build, and treat it as the process working correctly, not as a problem
  to hide.
- **Commit at the end of each phase**, once its verification criteria
  pass, with a message referencing the phase number. This is what makes
  "independently testable per phase" actually useful in practice — a
  clean rollback point if a later phase reveals a problem with an earlier
  one.
