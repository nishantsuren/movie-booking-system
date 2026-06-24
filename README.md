# Movie ticket booking system — Phase 0

Foundation scaffolding per `docs/implementation-plan.md`, Phase 0.

**Moving on to Phase 1 and beyond?** See `docs/claude-code-workflow.md` —
it has the setup steps and a ready-to-use prompt for Phase 1, plus
condensed guidance for every later phase. `CLAUDE.md` at the project root
is read automatically by Claude Code at the start of every session and
carries the project's established conventions forward, so you don't need
to re-explain them each time.

See `docs/design.md` for the full architecture this builds toward.

## What's here

- `docker-compose.yml` — **Postgres and Redis only.** One Postgres
  container hosting six logical databases (one per service — see the
  comment in `infra/postgres/init-databases.sh` for why a single
  container is the right call locally), one Redis instance. Both scoped
  under the `movieticket_` project name so this never collides with
  anything else Docker-related already on your machine.
- `scripts/dev.sh` — runs every backend service **natively on the host**,
  not containerized, in one shared virtualenv, against the Dockerized
  Postgres/Redis above. This is the fast-iteration path: `uvicorn
  --reload` picks up code changes immediately, no image rebuild between
  edits. Each service still has its own `Dockerfile`/`requirements.txt`
  for later containerized deployment (§15 of the design doc) — those
  just aren't used for local development.
- `shared/` — the idempotency and auth libraries every service depends
  on, each with real tests run against an actual Postgres instance.
- `routing/` — a working (not just stubbed) path-prefix forwarder.
- Everything else (`services/*`, `apps/*`, `local-cdn-mock/`) is a thin
  `/health`-only stub for now. Real logic starts in Phase 1.

## 1. Isolate this from anything else Docker-related on your machine

```bash
cp .env.example .env
```

Open `.env` and adjust `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` only if
`5433` or `6380` are themselves already taken on your machine — they're
deliberately *not* the Postgres/Redis defaults (`5432`/`6379`), specifically
so this can run alongside an existing local Postgres without touching it.

## 2. Bring up Postgres and Redis

```bash
docker compose up -d
```

This starts only the infrastructure — Postgres (six databases created
automatically on first boot) and Redis. No backend services are
containerized.

## 3. Run the backend services natively

```bash
./scripts/dev.sh
```

This creates a shared `.venv/` on first run, installs every service's
dependencies into it, and starts all five backend services plus the
local CDN mock plus the routing service as host processes — each with
`uvicorn --reload`, logging to `logs/<name>.log`. Same as
`./scripts/dev.sh startall`. Leave this running in its own terminal;
`Ctrl+C` stops everything cleanly.

If something else started it (a backgrounded/headless invocation with
no TTY to `Ctrl+C` — e.g. an agent), manage it from another terminal
instead:

```bash
./scripts/dev.sh list           # what's running, and its PID/port
./scripts/dev.sh kill <name>    # stop one service or worker by name
./scripts/dev.sh killall        # stop everything this script started
```

## 4. Verify it's actually working

In a third terminal:

```bash
# Each service directly
curl http://localhost:8001/health   # catalog
curl http://localhost:8002/health   # theatre
curl http://localhost:8003/health   # booking
curl http://localhost:8004/health   # payment
curl http://localhost:8005/health   # user
curl http://localhost:8006/health   # local-cdn-mock

# Through the routing service (proves it can resolve and reach every
# other service on localhost, not just that each one boots)
curl http://localhost:8000/health
curl http://localhost:8000/catalog/health
curl http://localhost:8000/theatre/health
curl http://localhost:8000/booking/health
curl http://localhost:8000/payment/health
curl http://localhost:8000/user/health

# Confirm Postgres has all six databases
docker exec movieticket_postgres psql -U movieticket -d postgres -c "\l" | grep _db

# Confirm Redis is reachable
docker exec movieticket_redis redis-cli ping
```

Every `/health` call should return `{"status":"ok",...}`.

## 5. Run the shared library tests

These run on your host machine against the Dockerized Postgres (so bring
the stack up first — step 2 is enough, `dev.sh` doesn't need to be
running for this):

```bash
cd shared
pip install -r requirements-dev.txt   # or reuse the .venv from dev.sh
PYTHONPATH=. pytest tests/ -v
```

You should see 10 tests pass — covering the idempotency `INSERT ... ON
CONFLICT` pattern against a real throwaway table (§11.1 of the design
doc), and the `AUTH_ENABLED` toggle in both states including role
rejection (§3.2). These are the two Phase 0 verification criteria from
the implementation plan, and they're the same tests CI (`.github/workflows/ci.yml`)
runs on every push.

## 6. Tear down

```bash
# Stop the native services: Ctrl+C in the dev.sh terminal, or from
# elsewhere: ./scripts/dev.sh killall

docker compose down          # stop and remove Postgres/Redis containers, keep data
docker compose down -v       # also remove the Postgres volume — full reset
```

## Exit criteria for Phase 0 (from the implementation plan)

- [x] `docker compose up` brings up all infra cleanly.
- [x] Each service has a working `/health` endpoint, reachable both
      directly and through the routing service.
- [x] Shared idempotency middleware has passing tests against a real
      throwaway table.
- [x] Shared auth middleware has passing tests for both `AUTH_ENABLED`
      states.

Once you've confirmed all four locally, Phase 1 (catalog + theatre
services, real schema, real endpoints) is next.
