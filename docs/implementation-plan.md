# Movie ticket booking system — implementation plan (v1)

Companion to `movie-ticket-booking-system-design.md` (v8). That document is the architecture reference — what gets built and why. This document is the execution roadmap — in what order, in what increments, and how each increment gets verified before the next one depends on it.

## Guiding principles

- **Every phase ships something that runs and can be independently verified** — via automated tests, direct API calls, or both — without requiring any later phase to exist. No phase is "half a feature."
- **Backend before frontend.** Both web apps come after the full booking saga works end-to-end via API, so UI bugs never get confused with backend bugs during the highest-risk phases.
- **The highest-risk mechanism gets isolated before it gets integrated.** Seat locking (Phase 4) is built and concurrency-tested as a standalone component before anything else depends on it — exactly as the design document itself prescribes (§5, build order).
- **Each phase is sized to be one focused Claude Code session.** Hand a phase's scope plus the relevant design-doc section(s) as context; the verification criteria are the session's acceptance test.
- **If reality diverges from the design doc during implementation, the design doc gets updated — not silently left stale.** This has already happened several times during design (the unique-index fix, the materialization retry behavior); expect the same during build.

A phase is done only when: (1) its automated tests pass, (2) it can be demonstrated end-to-end without any later phase existing, and (3) the design doc still accurately describes what was actually built.

---

## Phase summary

| # | Phase | Depends on | Relative complexity |
|---|---|---|---|
| 0 | Foundation & infra scaffolding | — | S |
| 1 | Catalog + theatre (read/admin) + local CDN mock + routing service | 0 | M |
| 2 | Freeform seat-layout authoring + draft lock | 1 | M |
| 3 | Showtime creation + seat materialization | 1, 2 | M |
| 4 | Seat locking mechanism, isolated | 0 | L |
| 5 | Booking creation, payment, confirmation | 3, 4 | L |
| 6 | Reconciliation sweep worker | 5 | M |
| 7 | User service + JWT issuance (not yet enforced) | 1 | S |
| 8 | Customer web app | 5, 7 | L |
| 9 | Admin web app | 2, 3, 7 | L |
| 10 | Auth hardening (`AUTH_ENABLED=true`, RBAC verification) | 7, 8, 9 | M |
| 11 | Resilience / chaos verification pass | 6, 10 | M |
| 12 | Load & capacity testing | 11 | M |
| 13 | Production-readiness checklist items | 11 | L (parallelizable) |

Complexity is relative sizing (S/M/L), not a time estimate — actual duration depends heavily on iteration speed and how much manual infra/testing happens between sessions.

---

## Phase 0 — Foundation & infra scaffolding

**Scope**: repo structure per design-doc Appendix B; `docker-compose` bringing up one Postgres instance per service, a Redis Cluster (even if minimally configured at this stage), and seed-script tooling; `shared/` libraries scaffolded — idempotency middleware (§11.1), auth/JWT middleware with the `AUTH_ENABLED` flag wired but defaulting false (§3.2), event-schema stubs (no real event bus yet, just the `EventPublisher` interface and a no-op/log implementation per §7); CI skeleton (lint + test runner, no deployment yet).

**Verification**:
- `docker-compose up` brings up all infra cleanly; each (future) service has a stubbed healthcheck endpoint.
- Shared idempotency middleware has unit tests proving the `INSERT ... ON CONFLICT` pattern behaves correctly against a throwaway table (duplicate key → same response, no duplicate row).
- Shared auth middleware has unit tests for both `AUTH_ENABLED=true` and `=false` paths.

**Exit criteria**: a developer can clone the repo, run one command, and have working infra with no services deployed on it yet.

---

## Phase 1 — Catalog + theatre (read/admin) + local CDN mock + routing service

**Scope**: catalog service (`MOVIE`, `MOVIE_RELEASE`, customer + admin endpoints per Appendix A/C, soft-delete non-cascading per §4.2); theatre service (`CITY`, `THEATRE`, `SCREEN`, customer + admin endpoints — **not** `SEAT_LAYOUT`/`SEAT_TEMPLATE` yet, that's Phase 2); local CDN mock (§3.1, both route groups); routing service forwarding to both (§3.2, no auth). No booking service, no user service yet.

**Verification**:
- Integration tests hitting the routing service: full CRUD lifecycle for movies, releases, theatres, screens via admin endpoints; customer browse endpoints return correctly filtered/city-scoped results.
- Seed script populates a realistic small dataset (a handful of cities, theatres, movies) and the data is reachable through routing.
- Asset upload via the admin path, then retrieval via the public path, round-trips correctly.
- Soft-delete test: deactivate a movie, confirm it disappears from customer browse but the record still resolves by ID (non-cascading per §4.2).

**Exit criteria**: an admin can fully populate catalog and theatre data via API calls alone, and a customer can browse it — no UI required yet, `curl`/Postman is sufficient.

---

## Phase 2 — Freeform seat-layout authoring + draft lock

**Scope**: `SEAT_LAYOUT`/`SEAT_TEMPLATE` tables (§4.5 schema — `id`/`label`/`x`/`y`/`seat_type`/`price_multiplier`, no row/column); draft create/edit/publish/clone endpoints (Appendix C); the draft edit lock (§4.6) — lock/heartbeat/release endpoints, lock-ownership enforcement on every mutating draft call. **No showtime or booking dependency yet** — this is testable purely as theatre-service CRUD plus the lock state machine.

**Verification**:
- Full layout lifecycle test: create draft with a flat seat list → edit individual and bulk-selected seats → publish → confirm `ACTIVE` and assigned to the screen in one transaction (§4.5).
- Draft lock contention test: two simulated admin sessions, second one blocked with `409` + current holder info while the first holds the lock.
- Staleness reclaim test: acquire a lock, stop sending heartbeats, advance time past the threshold (mock the clock), confirm a second session can now acquire it.
- Lock-ownership-not-just-existence test: acquire, let it go stale, attempt a save with the now-stale-holder's credentials — confirm rejection even though a lock record still technically exists.
- Clone test: publish a layout, clone it to a second screen, confirm fresh UUIDs with identical labels/positions/types.

**Exit criteria**: an admin can author and publish a complete, realistic seat layout (150+ seats, mixing the line/grid/single-seat placement patterns conceptually even before the canvas UI exists — i.e. the API accepts and correctly stores whatever flat seat list is sent) entirely via API.

---

## Phase 3 — Showtime creation + seat materialization

**Scope**: `SHOWTIME` table + admin CRUD in theatre service; booking service stood up for the first time, but **only** `SHOWTIME_SEAT` and the internal `materialize-seats` endpoint (§4.3, §5.3) — no locking, no `BOOKING` table yet. Theatre service calls the materialize endpoint with the §11.3 retry policy on showtime creation.

**Verification**:
- Create a showtime against a published layout → confirm matching `SHOWTIME_SEAT` rows exist in booking service's database with correct `label`/`position_x`/`position_y`/`seat_type`/`price`/`seat_template_id`.
- Idempotency test: call materialize twice for the same showtime (simulating a retry) → confirm the `UNIQUE (showtime_id, seat_template_id)` constraint prevents duplicates, second call is a no-op.
- Fail-closed test: stop booking service, attempt showtime creation, confirm it fails closed after exhausting retries rather than leaving an orphaned showtime with no seats (§4.3, §13).
- Deletion business rule test (today's version — no active bookings exist yet at this phase, so this just confirms the basic guard works before Phase 5 adds the harder concurrent case).

**Exit criteria**: this is the system's first real cross-service integration milestone — a showtime created through the admin API has a fully materialized, independently-queryable seat inventory in a different service's database, with no manual data entry into booking service.

---

## Phase 4 — Seat locking mechanism, isolated

**Scope**: the `SeatLocker` component only (§5.1, §5.4) — the Lua-script atomic multi-seat lock against Redis Cluster, TTL-based release, read-time reconciliation logic. Built and tested as a standalone library/adapter, called directly in tests — **not** wired into any HTTP endpoint yet. This is deliberately the narrowest-scoped, highest-rigor phase in the whole plan, matching the design document's own explicit instruction to prove this in isolation first.

**Verification**:
- Concurrency test: many parallel attempts to lock overlapping seat sets for the same showtime — assert exactly the correct winners, zero partial-lock states (proving the all-or-nothing Lua script behavior from §5.1).
- TTL test: acquire a lock, wait past expiry (or mock Redis TTL), confirm a new attempt against the same seats succeeds without manual intervention.
- Natural key-spreading sanity check (informational, not a hard pass/fail): confirm seats for one showtime land on different Redis Cluster hash slots, per §5.2's reasoning.
- Redis node-failure test: kill a Redis Cluster node mid-test, confirm client retry-with-backoff recovers once the replica promotes (§11.4) — no special fallback path should be needed.

**Exit criteria**: a test harness can run thousands of concurrent lock attempts against a seeded set of seats and the result is provably correct every time, before a single line of booking-orchestration code exists.

---

## Phase 5 — Booking creation, payment, confirmation

**Scope**: `BOOKING` table; `POST /bookings` wiring `SeatLocker` (Phase 4) into `BookingOrchestrator` (§7/§8's code sample) with idempotency (§11.1); payment service (mocked, §6); `POST /bookings/{id}/confirm` — the PK-scoped conditional update (§5.3); `DELETE /bookings/{id}` explicit cancel. This is where the full saga (lock → pending → pay → confirm) comes together for the first time.

**Verification**:
- Happy-path integration test: browse → select seats → create booking (`PENDING`) → pay → confirm → seat status is `BOOKED`, booking is `CONFIRMED`, denormalized snapshot fields (§4.2) populated correctly.
- Conflict test: two booking attempts for the same seats — one succeeds, one gets a clean `409` with the conflicting seat IDs.
- Expired-lock test: create a `PENDING` booking, advance time past the 10-minute window (mocked), confirm a payment-confirm attempt fails appropriately (this also previews what Phase 6 will clean up automatically).
- Idempotency test: replay a confirm call with the same idempotency key after success — confirm it returns the same result without re-executing.
- Payment-service-down test: confirm the circuit breaker trips and the booking stays safely `PENDING` rather than erroring destructively (§13).
- Showtime-deletion-race test (the harder version deferred from Phase 3): attempt to delete a showtime concurrently with an in-flight booking against it — confirm the synchronous check (§13) prevents the race.

**Exit criteria**: a complete movie-ticket purchase, from seat selection through paid confirmation, works end-to-end via API calls alone, with correctness verified under conflict and failure conditions, not just the happy path.

---

## Phase 6 — Reconciliation sweep worker

**Scope**: the periodic sweep worker (§5.4) — single-active-instance via Postgres advisory lock, the exact SQL and test list already specified in the design document.

**Verification** (reusing §5.4's proof-of-correctness list directly, since it was written with this phase in mind):
- Kill the active instance mid-batch, confirm a standby takes over within one poll interval with no half-processed booking (both updates share a transaction).
- Start three replicas simultaneously, confirm exactly one acquires the advisory lock.
- Race a concurrent payment-confirm against the sweep's selection of the same booking, confirm confirmation always wins.
- Re-run the sweep query immediately after a successful pass, confirm zero rows affected.
- Seed a backlog of expired `PENDING` bookings (simulate the sweep having fallen behind), confirm it drains within bounded batches without degrading the booking API's normal request path.

**Exit criteria**: an abandoned booking's seats become available again automatically, with no manual cleanup, verified under both normal operation and induced worker failure.

---

## Phase 7 — User service + JWT issuance

**Scope**: `USER` table with `role`; register/login issuing a JWT with the role claim (§3, Appendix A). `AUTH_ENABLED` remains `false` everywhere else at this point — this phase establishes the capability without yet flipping the switch that makes everything else depend on it.

**Verification**:
- Register/login round-trip returns a valid JWT with the correct role claim.
- Regression test across every endpoint built in Phases 1–6: confirm `AUTH_ENABLED=false` still bypasses cleanly and nothing built so far has accidentally started requiring a token.

**Exit criteria**: real accounts and real tokens exist and are correct, without yet being load-bearing for anything else in the system — isolating "does auth work" from "does enforcing auth break something else," which Phase 10 addresses deliberately.

---

## Phase 8 — Customer web app

**Scope**: the customer SPA (§3) — browse, showtime/seatmap view, seat selection, booking, mocked payment, confirmation. Calls through the routing service; loads from the local CDN mock.

**Verification**:
- E2E browser test suite (Playwright/Cypress) covering the full happy path against the real backend built in Phases 1–6.
- Key error-state coverage: seat conflict mid-selection, expired lock countdown reaching zero, payment failure — confirm the UI surfaces these clearly rather than failing silently.

**Exit criteria**: a person can complete a real ticket purchase through the browser, not just through API calls.

---

## Phase 9 — Admin web app

**Scope**: the admin SPA (§3) — movie/theatre/screen CRUD forms, showtime management, and the centerpiece: the seat-layout canvas editor (§4.5 — line/grid/curve/single-seat placement tools, multi-select bulk edit) plus the draft-lock UX (§4.6 — showing lock status, blocking edits when another admin holds it, surfacing staleness).

**Verification**:
- E2E test authoring a complete, realistic seat layout through the canvas tools end-to-end, publishing it, and confirming it renders correctly in the customer app's seatmap (Phase 8) — this is the strongest possible integration check between the two frontends.
- Concurrent-edit UX test: two simulated admin sessions against the same draft, confirm the second is blocked with a clear message, and can proceed once the first releases or goes stale.

**Exit criteria**: a non-technical admin user could plausibly onboard a new theatre — create it, build its seat layout visually, schedule showtimes — without ever touching the API directly.

---

## Phase 9.5 — TheatreIntegration: the aggregator lock

**Scope**: added after Phase 9 on an architectural correction (design v18, §5.7) — a real aggregator doesn't own the seat inventory, the theatre's own ticketing system does, and the same seat is simultaneously bookable via the theatre's own site, other aggregators, and box-office terminals. The `TheatreIntegration` interface, a `MockTheatreIntegration` (always succeeds locally), and the second leg of the two-phase lock wired into `BookingOrchestrator.select_seats`/`confirm`/`cancel`, plus the sweep worker's expiry path. Bounded: interface, mock, wiring, and failure tests only — real per-POS-system adapters are future work (§16.8).

**Verification**:
- Full existing regression suite, zero changes needed to any pre-existing test.
- New failure-path tests: hold conflict (409, Redis lock released); theatre API timeout (503, Redis lock released, circuit breaker trips); confirm_hold failure after a successful hold+payment (Outbox retries independently, booking stays CONFIRMED); release_hold failure on both cancel and sweep expiry (Outbox retries, booking/seat state stays correctly CANCELLED/EXPIRED); a null `theatre_hold_id` (pre-v18 booking) skips the external call cleanly on both confirm and cancel.

**Exit criteria**: the existing within-platform booking flow behaves identically to before this phase from the customer's perspective when the theatre integration succeeds (the common case); when it doesn't, the failure is surfaced cleanly (409/503) and never leaves Redis or Postgres in an inconsistent state.

---

## Phase 10 — Auth hardening

**Scope**: flip `AUTH_ENABLED=true` across every backend service; build out the full role-based access-control test matrix (§3.2, §15).

**Verification**:
- A complete access-control matrix: every endpoint × every role (including unauthenticated) → expected allow/deny, run as an automated test suite, not a manual checklist.
- Confirm both web apps still function correctly end-to-end once auth is enforced (re-run the Phase 8/9 E2E suites against the now-authenticated backend).
- Confirm the admin-only endpoints (Appendix C) genuinely reject non-admin tokens, not just unauthenticated requests.

**Exit criteria**: the system behaves identically from a functional standpoint with `AUTH_ENABLED=true` as it did with it `false` — the only observable difference is that unauthorized requests are now correctly rejected.

---

## Phase 11 — Resilience / chaos verification pass

**Scope**: systematic verification of every row in the design document's §13 failure table that wasn't already covered by an earlier phase's tests. Many already are (sweep-worker failover in Phase 6, materialization retry in Phase 3, draft-lock staleness in Phase 2) — this phase fills the remaining gaps: Postgres primary failover, Redis full-cluster outage, a booking-service instance crashing mid-request, and the duplicate-request/idempotency guarantees holding under genuinely concurrent retries rather than just sequential test cases.

**Verification**: each remaining §13 row gets an explicit, scripted chaos test (e.g. `docker kill` on a Postgres primary mid-transaction, sever the booking service's network path to Redis entirely) with an assertion on the expected degraded-but-correct behavior, not just "the system didn't crash."

**Exit criteria**: every claim made in §13 of the design document has a test proving it, not just a sentence asserting it.

---

## Phase 12 — Load & capacity testing

**Scope**: load-testing scripts (k6, Locust, or equivalent) targeting the figures in §2 — sustained and burst load for catalog/showtime browsing, seatmap views, booking creation, and a simulated hot-showtime stampede specifically exercising §5.2's natural Redis key-spreading and the resulting clean `409` conflict behavior at volume.

**Verification**:
- Sustained-load runs at the §2 figures, confirming latency stays within reasonable bounds and the reconciliation worker (Phase 6) keeps sweep lag bounded under realistic booking-creation volume.
- A scripted stampede test against one showtime's full seat inventory, confirming no double-bookings occur under real concurrent load (not just the unit-level concurrency test from Phase 4) and that Redis Cluster load distributes as expected.
- Data-volume sanity check: seed a proportionally-scaled (not necessarily full 45M-row) dataset and confirm partitioning (§12) keeps query performance acceptable as `SHOWTIME_SEAT` grows.

**Exit criteria**: the capacity numbers in §2 aren't just planning assumptions — they're load-tested and either confirmed or used to correct the design document.

---

## Phase 13 — Production-readiness checklist items

**Scope**: the §15 checklist, each item largely independent of the others and parallelizable across sessions/people: real API gateway replacing the routing service's slot; real event bus replacing the no-op `EventPublisher`; observability stack (§14 — metrics, tracing, structured logging, alerting); secrets management; the local CDN mock replaced by real static hosting and a real CDN + object storage.

**Verification**: per item — gateway correctly enforces auth/rate-limiting/WAF where the routing service didn't; event bus reliably delivers `BookingConfirmed` and related events to a real (even if minimal) notification-service consumer; dashboards populated with the metrics named in §14; a chaos test confirms secrets aren't recoverable from logs or error responses; the real CDN serves both SPA bundles and assets correctly with the same URL-based contract the mock used, requiring zero application code changes.

**Exit criteria**: every item in the design document's §15 checklist is implemented and independently verified — this is the last phase before the system is genuinely production-deployable, not just feature-complete.
