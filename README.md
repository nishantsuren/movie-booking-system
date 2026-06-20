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

- `docker-compose.yml` — one Postgres container hosting six logical
  databases (one per service, per the design doc's database-per-service
  model — see the comment in `infra/postgres/init-databases.sh` for why a
  single container is the right call locally), one Redis instance, and
  thin FastAPI stub containers for every backend service plus the routing
  service. Every container, network, and volume is scoped under the
  `movieticket_` project name so this never collides with anything else
  Docker-related already on your machine.
- `shared/` — the idempotency and auth libraries every service will
  depend on from Phase 1 onward, each with real tests (not mocked) run
  against an actual Postgres instance.
- `routing/` — a working (not just stubbed) path-prefix forwarder. It's
  cheap to build now and gives a genuine wiring check for the whole stack.
- Everything else (`services/*`, `apps/*`, `local-cdn-mock/`) is a thin
  `/health`-only stub. Real logic starts in Phase 1.

## 1. Isolate this from anything else Docker-related on your machine

```bash
cp .env.example .env
```

Open `.env` and adjust `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` only if
`5433` or `6380` are themselves already taken on your machine — they're
deliberately *not* the Postgres/Redis defaults (`5432`/`6379`), specifically
so this can run alongside an existing local Postgres without touching it.

## 2. Bring up the stack

```bash
docker compose up --build
```

This builds and starts: Postgres (with six databases created
automatically on first boot), Redis, all five backend service stubs, the
local CDN mock, and the routing service.

## 3. Verify it's actually working

In a second terminal, once everything reports healthy:

```bash
# Each service directly
curl http://localhost:8001/health   # catalog
curl http://localhost:8002/health   # theatre
curl http://localhost:8003/health   # booking
curl http://localhost:8004/health   # payment
curl http://localhost:8005/health   # user
curl http://localhost:8006/health   # local-cdn-mock

# Through the routing service (proves cross-container networking works,
# not just that each container starts)
curl http://localhost:8000/health            # routing's own health
curl http://localhost:8000/catalog/health    # forwarded to catalog
curl http://localhost:8000/theatre/health    # forwarded to theatre
curl http://localhost:8000/booking/health    # forwarded to booking
curl http://localhost:8000/payment/health    # forwarded to payment
curl http://localhost:8000/user/health       # forwarded to user

# Confirm Postgres has all six databases
docker exec movieticket_postgres psql -U movieticket -d postgres -c "\l" | grep _db

# Confirm Redis is reachable
docker exec movieticket_redis redis-cli ping
```

Every `/health` call should return `{"status":"ok",...}`. The forwarded
calls succeeding is the real proof here — it means containers can resolve
and reach each other by service name on the `movieticket_net` network, not
just that each one boots in isolation.

## 4. Run the shared library tests

These run on your host machine against the Dockerized Postgres (so bring
the stack up first):

```bash
cd shared
pip install -r requirements-dev.txt   # or use a venv, your call
PYTHONPATH=. pytest tests/ -v
```

You should see 10 tests pass — covering the idempotency `INSERT ... ON
CONFLICT` pattern against a real throwaway table (§11.1 of the design
doc), and the `AUTH_ENABLED` toggle in both states including role
rejection (§3.2). These are the two Phase 0 verification criteria from
the implementation plan, and they're the same tests CI (`.github/workflows/ci.yml`)
runs on every push.

## 5. Tear down

```bash
docker compose down          # stop and remove containers, keep data
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
