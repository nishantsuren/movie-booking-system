# Architecture document

A BookMyShow-style movie ticket booking platform: Python/FastAPI
microservices, one Postgres database per service, Redis for the
high-throughput seat lock. This document is a synthesized architectural
reference distinct from `docs/design.md` (the build-order changelog that
accumulated decisions phase-by-phase) — read this for "how the system
works today," `design.md` for "why it ended up this way."

---

## 1. Services — role and responsibility

| Service | Owns | Responsibility |
|---|---|---|
| **catalog** | `MOVIE`, `MOVIE_RELEASE` | Movie metadata and per-city release windows. Customer browse (`GET /movies`, scoped to a city's currently-running releases) and admin CRUD (create/update/soft-delete movies, manage releases). |
| **theatre** | `CITY`, `THEATRE`, `SCREEN`, `SEAT_LAYOUT`, `SEAT_TEMPLATE`, `SHOWTIME` | Theatre/screen directory, the freeform seat-layout authoring tool with its draft lock, and showtime scheduling (which triggers seat materialization in booking). Customer browse (cities/theatres/showtimes) and the much larger admin surface (theatre/screen CRUD, seat-layout draft/lock/publish/clone, showtime CRUD/activate/deactivate). |
| **booking** | `SHOWTIME_SEAT`, `SHOWTIME_META`, `BOOKING`, `PENDING_THEATRE_CALL` | The seat lock and the booking saga: seat selection, mocked payment confirmation, cancellation. Owns the two-phase lock (Redis + external `TheatreIntegration`), the seatmap read path (this system's highest-volume read), and the Outbox that retries calls to the theatre's own system. Hosts three standalone workers (§2). Also `POST /showtimes/{id}/find-seats` (Phase 9.6) — ranked adjacent-seat groups for the AI agent, business logic only (`application/seat_finder.py`), no new table. |
| **agent-service** | Nothing (stateless; sessions are in-memory) | AI booking agent (Phase 9.6, `docs/ai-agent-requirements.md`): a state machine (`dialogue_manager.py`) that owns every decision/API call/response, with a local LLM (Llama 3.2 3B via Ollama) called at most once per turn purely to extract intent + entities as JSON. Calls the other services exclusively through routing, same as the frontends. |
| **payment** | `PAYMENT` | Mocked payment processing — always succeeds. Exists as a separate service specifically so booking's confirm path exercises a real cross-service call and a real circuit breaker, even though there's no real payment gateway behind it yet. |
| **user** | `app_user` (named to dodge `user`, a reserved Postgres word) | Registration/login, password hashing, JWT issuance with a role claim (`CUSTOMER`/`ADMIN`). The only service that *mints* tokens; every other service only *verifies* them via the shared `shared/auth/auth.py` library. |
| **routing** | Nothing (stateless) | A path-prefix forwarder (`/catalog/*` → catalog, `/theatre/*` → theatre, etc.) standing in for a real API gateway. Adds CORS headers (needed for both web frontends, since CORS is a browser-only restriction curl never exercises). Deliberately "dumb" — no auth, no rate limiting; that's the slot a real gateway fills in production (§15). |
| **local-cdn-mock** | `ASSET` (its own small metadata table) | Local-dev-only stand-in for a real CDN + object storage: serves both frontend SPA bundles (`/`, `/admin/`, with SPA fallback so client-side routing survives a direct navigation/reload) and uploaded assets (`GET/POST /assets`, content-addressable). Never exists in production — replaced by real static hosting (§15). |

Two frontends (not backend services, but part of the system): **customer-web** (browse → seatmap → book → pay → confirm) and **admin-web** (movie/theatre/screen/showtime CRUD + the seat-layout canvas editor). Both call exclusively through routing, never a backend service directly.

---

## 2. Workers — role and responsibility

All three live in `services/booking/adapters/` and run as standalone processes (not part of the FastAPI app — a different scaling/redundancy profile than request handling). Each uses the same pattern: **N replicas, exactly one active**, elected via a dedicated Postgres advisory lock key, automatic failover to a standby within roughly one poll interval if the active instance dies.

| Worker | Advisory lock key | Responsibility |
|---|---|---|
| `reconciliation_sweep.py` | `84236501` | The *sole* mechanism that flips an abandoned `PENDING` booking to `EXPIRED` and its locked seats back to `AVAILABLE`. Polls every 15–30s for bookings past `expires_at`. Also enqueues a `RELEASE_HOLD` Outbox entry for any expired booking that had an external theatre hold. |
| `theatre_outbox_relay.py` | `84236502` | Calls the theatre's system's `confirm_hold`/`release_hold` for rows in `pending_theatre_call` — the *fallback* path only, used when `confirm()`'s own synchronous attempt fails (§6), or for `release_hold` always (cancel/sweep stay purely async). Bounded retries with exponential backoff; moves a row to `FAILED` once attempts are exhausted. For an exhausted `CONFIRM_HOLD` specifically, also raises `TheatreConfirmationAbandoned` and publishes `TheatreConfirmationFailedEvent` + `RefundRequestedEvent` — the theatre never learned to honor a booking already paid for, so the customer is notified and the payment queued for refund. |
| `theatre_availability_sync.py` | `84236503` | Periodically pulls each showtime's seat availability from the theatre's system (`sync_availability`) and reconciles any seat this system still believes is `AVAILABLE` but the theatre now reports as taken (e.g. booked via another channel) — the read side of the "sync-based shadow inventory" approach (§7 of design.md's §5.7). |

Why three *separate* workers rather than one combined loop: each has an independent failure mode and cadence (the sweep cares about booking expiry, the relay about a completely different table/retry cadence, the sync job about external drift on a much slower interval) — bundling them would mean one's bug or backlog blocks the others, and a single advisory lock key would serialize work that has no reason to be serialized.

---

## 3. Service/worker dependencies, and why

```
customer-web, admin-web  →  routing  →  catalog / theatre / booking / payment / user
                                              ↑
                          theatre  ───────────┘  (materialize-seats, synchronous)
                          theatre  ───→  catalog  (movie details, one live call per browse request)
                          booking  ───→  payment   (get_payment, on confirm)
                          booking  ───→  Redis      (seat lock, §5.1)
                          booking  ───→  "theatre's own system" (TheatreIntegration — mocked
                                          in-process today; a real adapter would be a genuine
                                          network call, §16.8)
        reconciliation_sweep, theatre_outbox_relay,
        theatre_availability_sync  ───→  booking_db only (no service-to-service calls)
```

- **Frontends → routing only, never a backend service directly.** Single ingress point, matches what a real API gateway would enforce in production; also the only place CORS needs to be configured.
- **theatre → catalog** (`GET /movies/{movie_id}/showtimes`'s enrichment call): a *browse-time* read, picked once per date/city choice by a human, not once per click — low frequency, so paying for a live cross-service call here is acceptable. Contrast with booking's seatmap read (next point), which is the system's *highest-volume* read and deliberately avoids exactly this kind of call.
- **theatre → booking** (`POST /internal/showtimes/{id}/materialize-seats`, called from `create_showtime`): synchronous, with bounded retry + exponential backoff (§11.3), and *fail-closed* — if materialization doesn't succeed, showtime creation itself fails and rolls back. The alternative (create the showtime first, materialize asynchronously) would allow a showtime to exist with zero bookable seats, an inconsistent state nothing else in the design accounts for.
- **booking → payment**: a real HTTP call wrapped in a circuit breaker (`CircuitBreaker`, §6) — if payment is down, confirm fails fast (503) rather than hanging, and the booking stays safely `PENDING` (its own TTL bounds the customer-facing impact).
- **booking → Redis**: the fast, within-platform seat lock (§5.1) — sub-millisecond, atomic across every seat in one request via a Lua script. This is necessary *in addition to* Postgres's own conditional updates (§5.3) because Postgres alone, under a stampede, would still let many requests race into expensive write contention before any of them loses; Redis rejects all but one cheaply, before Postgres is ever touched.
- **booking → "theatre's own system"**: conceptually a second, *external* lock (§5.7) — the Redis lock only protects this platform's own concurrent users, not a seat booked via the theatre's own website, another aggregator, or the box office at the same moment. Today `MockTheatreIntegration` always succeeds, so this dependency is currently in-process, not a real network hop — but the `TheatreIntegration` Protocol is exactly the seam a real per-POS-system adapter (Vista, Moviebook, ...) would plug into without any orchestrator changes.
- **Workers → `booking_db` only**: by design, none of the three workers call any other service. The sweep worker and outbox relay both only ever touch rows already written by the request path that enqueued them; the availability-sync job calls `TheatreIntegration.sync_availability()`, which — like `hold_seats`/`confirm_hold`/`release_hold` — is currently mocked, but in production *would* be the one place a worker makes an outbound call, to the theatre's system specifically, never to another of this platform's own services.

---

## 4. User flows, design choices, and failure scenarios

### 4.1 Customer: browse → book → pay → confirm

1. **Browse** (`GET /catalog/movies?city=`, `GET /theatre/movies/{id}/showtimes?city=&date=`) — pure reads, no locking. Design choice: the showtimes-for-movie response is *enriched* with the movie's own details server-side (one live catalog call), so the frontend doesn't have to orchestrate two calls itself.
2. **Seatmap** (`GET /booking/showtimes/{id}/seatmap`) — the system's highest-volume read (~4M/day vs ~250K bookings/day, §2). Served entirely from booking's own `SHOWTIME_SEAT` + `SHOWTIME_META`, zero live cross-service calls. Failure mode mitigated: without `SHOWTIME_META`'s cache, this page would need a live call to theatre (and theatre to catalog) on *every* seatmap view — exactly the cost profile this design refuses to pay on its hottest path.
3. **Select seats** (`POST /booking/bookings`) — see §6 in full detail below. Two-phase lock (Redis, then the external theatre system), `PENDING` booking row, 10-minute hold.
4. **Pay** (`POST /payment/payments`, mocked, always succeeds) — a real cross-service call exists here specifically so the circuit-breaker and "payment down" failure path (§4.3 below) are real, not aspirational.
5. **Confirm** (`POST /booking/bookings/{id}/confirm`) — flips `SHOWTIME_SEAT` to `BOOKED` and `BOOKING` to `CONFIRMED` in one transaction, releases the Redis lock, then calls `theatre.confirm_hold()` *synchronously* (falling back to the Outbox only if that call itself fails, v21 — see §6 for why). Design choice (v14): confirm gates purely on *state* (must still be `LOCKED`/`PENDING`), not a clock comparison of its own, since the sweep worker is the sole wall-clock authority once it exists — a customer who pays a moment after the displayed countdown reaches zero, but before the sweep's own pass, still completes their purchase. The customer-facing UI surfaces this "grace window" explicitly rather than asserting a hard deadline it can't actually guarantee.
6. **Cancel** (`DELETE /booking/bookings/{id}`, `PENDING` only) — releases the Redis lock, reverts seats to `AVAILABLE`, enqueues `RELEASE_HOLD`.

### 4.2 Admin: theatre/movie/showtime management

1. Create theatres/screens/movies (idempotent creates, §5).
2. Author a seat layout via the canvas (line/grid/curve/single tools, all client-side, §7) → publish a draft.
3. Schedule a showtime against a screen's `ACTIVE` layout → theatre synchronously calls booking to materialize `SHOWTIME_SEAT` rows → activate it.

### 4.3 Failure scenarios and how they're mitigated

| Scenario | Risk without mitigation | Mitigation |
|---|---|---|
| Two users race for the same seat (within this platform) | Double-booking | Redis atomic multi-key lock (§5.1) + Postgres conditional `UPDATE` as a second, independent backstop (§5.3) |
| Same seat booked via the theatre's own site / another aggregator | Double-booking across channels | External `hold_seats()` call as a second lock leg (§5.7); conflict → clean 409, Redis lock compensated/released |
| Theatre API times out or 5xx during `hold_seats` | Booking creation hangs or half-completes | Bounded-timeout call, release the Redis lock, return 503 (retryable); circuit breaker trips after repeated failures so the platform stops accumulating held Redis locks against a degraded theatre API |
| `confirm_hold`'s *synchronous* attempt fails right after the booking is already correctly `CONFIRMED` | A theatre-side hold lingers unconfirmed | Falls back to the Outbox (`pending_theatre_call`) + independent relay with bounded backoff; booking/seat state is never at risk, since it was already correct before the fallback ever triggers |
| `release_hold` fails after `CANCELLED`/`EXPIRED` (always async — cancel/sweep never attempt it synchronously) | A theatre-side hold lingers past when it should release | Same Outbox + relay; not time-critical the same way confirm is, since the seat just stays held slightly longer, never double-booked |
| The Outbox relay itself exhausts retries on a `CONFIRM_HOLD` entry | Theatre never learns to honor a booking already paid for — real double-booking risk on their side | `pending_theatre_call.status = 'FAILED'` (manual reconciliation) + `TheatreConfirmationAbandoned` raised + `TheatreConfirmationFailedEvent`/`RefundRequestedEvent` published (mock events) — customer notified, payment queued for refund rather than left standing |
| A customer abandons checkout (closes the tab) | Seat locked forever | Redis TTL self-expires; the sweep worker is what makes that visible in Postgres (`PENDING`→`EXPIRED`, seat back to `AVAILABLE`) within one poll interval |
| Sweep worker's active instance crashes mid-batch | Stuck/duplicated sweep work | Postgres advisory lock auto-releases on connection death; a standby takes over within ~one poll interval; each sweep pass's own SQL re-checks state so a half-done pass is never double-applied |
| Payment service down/slow | Booking creation or confirm hangs | Circuit breaker (booking↔payment); booking stays safely `PENDING`, its own TTL bounds the blast radius |
| Showtime creation's materialize-seats call fails | A showtime could exist with zero bookable seats | Fail-closed: showtime creation itself fails and rolls back; bounded retry with backoff first (§11.3) |
| Duplicate/retried request (network retry, double-click, etc.) | Double-booking, duplicate entities, double charge | Idempotency everywhere creates happen (§5) |
| Two admins edit the same seat-layout draft simultaneously | Lost-update / conflicting edits | Pessimistic, heartbeat-based draft lock (§4.6/§7) |
| Movie/theatre soft-deleted while a showtime/booking still references it | Could retroactively invalidate a real booking | Soft-delete never cascades; `BOOKING` snapshots its own `movie_title`/`seat_labels`/`price_paid` independent of the source record's current state |
| A seat taken on another portal between syncs | Briefly appears bookable here | Bounded staleness window (sync interval, e.g. 60s) — the *consequence* is a clean 409 at `hold_seats()` time, not a double-booking; the shadow inventory is a cache for the read path, never the actual lock |

---

## 5. Idempotency

Every create-type endpoint across every service uses the same primitive (`shared/idempotency/idempotency.py`'s `IdempotentWriter.insert_or_get`): a single atomic `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING *`, with a short bounded retry-read loop for the one legitimate race (a concurrent identical request's transaction hasn't committed yet when this one detects the conflict). **No separate check-then-write window, no shared Redis idempotency store** — the duplicate check and the write are the same database operation.

**The key is always derived server-side**, never client-supplied: a deterministic SHA-256 hash of the request's identity-defining fields (each service has its own small `derive_idempotency_key(*parts)` helper, duplicated per service rather than shared — same convention as everything cross-service in this codebase). The client carries no key-generation or retry-tracking burden; a genuine retry of the *same logical request* always re-derives the *same* key and is deduplicated automatically.

| Entity | Identity-defining fields | Notes |
|---|---|---|
| `MOVIE` | title + duration + language | |
| `MOVIE_RELEASE` | movie + city + release date | |
| `THEATRE` | city + name | |
| `SCREEN` | theatre + name | |
| `SHOWTIME` | screen + start_time | |
| `ASSET` | hash of the uploaded bytes themselves | Content-addressable — identical bytes uploaded twice never duplicate storage |
| `BOOKING` | showtime + user + **sorted, comma-joined** seat IDs | The one case needing canonicalization before the general pattern applied (deferred from v9, resolved v12) |
| `PAYMENT` | `booking_id` itself | Already a single natural key — no derived hash needed |
| `app_user` (register) | email | **Deliberate exception** — see below |

**`BOOKING`'s unique constraint is partial, not plain**: `UNIQUE (idempotency_key) WHERE status IN ('PENDING', 'CONFIRMED')`. Unlike a movie or theatre, the same `(user, showtime, seats)` triple is a *legitimately recurring* identity — a hold expires or is cancelled, and the same user retrying the same seats later must succeed, not be permanently blocked by a dead row. The partial index frees the key the instant a booking reaches a terminal state.

**`POST /auth/register` is the deliberate exception** to "a conflict returns the existing row": email is the natural key, but password isn't part of it, so a conflict can't be distinguished from a genuine retry versus a different person targeting an already-taken email. Returning the first registrant's data on conflict would be a real account-confusion bug — so this one returns `409`, not `200`/`201` with the existing row.

**One acknowledged gap**: seat-layout draft creation has *no* idempotency key at all (confirmed as deliberate, not an oversight) — a payload hash of `screen_id + name` isn't a safe dedup key here, since the same screen legitimately gets a brand-new draft on every re-edit cycle, often reusing the same name; a dedup hit would silently hand back a stale, possibly-now-`ACTIVE` row instead of a fresh draft.

---

## 6. The seat booking flow, in detail

`POST /bookings` → `BookingOrchestrator.select_seats()`, then `POST /bookings/{id}/confirm` → `.confirm()`, then optionally `DELETE /bookings/{id}` → `.cancel()`. One orchestrator instance is constructed per request in `main.py`'s `_build_orchestrator(conn)`, sharing one Postgres connection across every repository it's given — the orchestrator itself never calls `commit()`/`rollback()`; the route handler owns that transaction boundary.

### Classes involved

| Class | File | Role |
|---|---|---|
| `BookingOrchestrator` | `application/booking_orchestrator.py` | The saga itself. Holds no state across calls; everything it needs is passed in via the repositories/clients below. |
| `Booking` (dataclass), `BookingStatus` (enum) | `domain/booking.py` | Plain data — `PENDING`/`CONFIRMED`/`EXPIRED`/`CANCELLED`. State transitions are conditional SQL `UPDATE`s in the adapters below, *not* an in-process state machine — consistent with how every service here treats its database as the actual source of truth. |
| `ShowtimeNotMaterialized`, `SeatsUnavailable`, `BookingNotFound`, `InvalidBookingState`, `BookingHoldExpired`, `ConfirmConflict`, `PaymentNotValid` | `domain/booking.py` | Domain exceptions, each mapped to a specific HTTP status in `main.py` (404/409/503 as appropriate) — never a generic 500 for an expected outcome. |
| `TheatreIntegration` (Protocol), `HoldResult`, `SeatStatus`, `TheatreIntegrationUnavailable` | `domain/theatre_integration.py` | The external-lock interface (§5.7) and its data shapes. `BookingOrchestrator` depends on the Protocol, never the concrete mock — the same Dependency Inversion pattern as every other collaborator here. |
| `PostgresSeatRepository` | `adapters/postgres_seat_repository.py` | `SHOWTIME_SEAT` reads/writes: `get_available_for_booking` (computes `is_effectively_available` server-side — `AVAILABLE`, or `LOCKED` with an already-expired hold, §5.4), `lock_seats` (conditional `AVAILABLE`→`LOCKED`), `mark_booked` (conditional `LOCKED`→`BOOKED`, §5.3's actual correctness guarantee), `release_to_available`. |
| `RedisSeatLocker` | `adapters/redis_seat_locker.py` | The within-platform lock (§5.1/§5.2). `acquire()` runs one Lua script doing an all-or-nothing check-and-`SET NX EX` across every requested seat key in a single atomic round trip; `release()` deletes them. Keys are hash-tagged on `showtime_id` so one showtime's seats always colocate on one Redis Cluster slot. |
| `TheatreIntegration` impl: `MockTheatreIntegration` | `adapters/mock_theatre_integration.py` | Always succeeds by default (`hold_mode="success"`); its failure knobs exist purely to drive failure-path tests, never touched in normal operation. `hold_seats` wraps its own logic in a `CircuitBreaker`. |
| `CircuitBreaker` | `adapters/circuit_breaker.py` | Generic closed/open/half-open breaker, parametrized on which exception type trips it. Shared between `PaymentClient` and the theatre integration — extracted once both needed the identical state machine. |
| `PaymentClient` | `adapters/payment_client.py` | HTTP client for the payment service, also wrapped in its own `CircuitBreaker` instance (trips on `PaymentServiceUnavailable`). |
| `PostgresBookingRepository` | `adapters/postgres_booking_repository.py` | `BOOKING` reads/writes: `get_live_by_idempotency_key`, `create_pending` (the `INSERT ... ON CONFLICT` against the partial unique index — can't go through the shared `IdempotentWriter` since that helper assumes a plain unique constraint), `mark_confirmed`, `mark_cancelled`. |
| `PostgresOutboxRepository` | `adapters/postgres_outbox_repository.py` | `pending_theatre_call` reads/writes: `enqueue` (called from inside the same transaction as confirm/cancel), `fetch_due`/`mark_done`/`record_failure` (used only by the relay worker, on its own separate connection). |
| `ShowtimeMetaRepository` | `adapters/showtime_meta_repository.py` | `SHOWTIME_META` reads: `get_movie_title` (used by `select_seats` to confirm the showtime was actually materialized before doing anything else). |

### `select_seats(showtime_id, seat_ids, user_id, idempotency_key)`

1. **Idempotency check** — a live (`PENDING`/`CONFIRMED`) booking under this key returns immediately, touching neither Redis nor `SHOWTIME_SEAT`.
2. **Materialization check** — no `SHOWTIME_META` row means this showtime was never materialized; fail fast (404) rather than discover it deep in the seat-lock logic.
3. **Availability check** — every requested seat must exist and be *effectively* available (computed server-side, §5.4).
4. **Redis lock** (`RedisSeatLocker.acquire`) — the fast, within-platform leg. Conflict → 409 with the actual conflicting seat IDs.
5. **External hold** (`TheatreIntegration.hold_seats`) — the second leg (§5.7). A theatre-side conflict or an unavailable theatre API both *compensate step 4* by releasing the Redis lock before propagating the failure (409 or 503 respectively) — this compensating-release-on-failure structure is itself the Saga pattern.
6. **Insert `PENDING` booking** (`PostgresBookingRepository.create_pending`), carrying the snapshotted `movie_title`/`seat_labels`/`price_paid` and the external `theatre_hold_id`. A lost race here (a true concurrent duplicate) releases both locks just taken and returns the winner's row instead.
7. **Lock the seats in Postgres** (`PostgresSeatRepository.lock_seats`) — the defense-in-depth reflection of the Redis lock. A count mismatch here (Redis said yes, Postgres didn't agree on every seat) fails closed: release everything, 409, rather than persist a half-correct booking.

### `confirm(booking_id, payment_id)`

State-gated only (no clock check, v14) — must still be `PENDING`. Validates the payment via `PaymentClient` (may raise `PaymentNotFound`/`PaymentServiceUnavailable`), then one transaction: `mark_booked` (conditional `LOCKED`→`BOOKED`) immediately followed by `mark_confirmed` (conditional `PENDING`→`CONFIRMED`). A zero-affected-rows result from `mark_booked` means either a concurrent confirm already won (return its result idempotently) or the sweep already expired this hold (`BookingHoldExpired`). Releases the Redis lock, then — if `theatre_hold_id` is not `None` — calls `theatre.confirm_hold()` **synchronously** (v21): a confirm landing near the tail of the hold's TTL has no slack left, and only ever going through the Outbox relay's poll interval could let the theatre's own hold expire before the relay got to it, a real cross-channel double-booking risk. Only on a `TheatreIntegrationUnavailable` from that synchronous call does it fall back to `self._outbox.enqueue("CONFIRM_HOLD", ...)` — the Outbox is the fallback, not the primary path. Publishes a `BookingConfirmedEvent` regardless.

### `cancel(booking_id)`

`PENDING`-only, conditional `mark_cancelled`, then `release_to_available` on the seats, Redis release, and a `RELEASE_HOLD` Outbox enqueue (same null-skip).

---

## 7. Seat layout design

**Fully freeform — no stored row/column structure anywhere.** Every seat is an independent record: `id`, `label` (free text), `position_x`/`position_y`, `seat_type`, `price_multiplier`. This trades away free "what's adjacent to this seat" lookups (would need runtime x/y proximity math if ever needed) for the ability to represent *any* real theatre's actual shape — curved rows, gaps, balconies — not just clean rectangular grids.

### Authoring (Builder pattern)

The admin canvas (`apps/admin-web`) provides four placement tools, each a pure function over an in-progress client-side seat list — **nothing about "a row" or "a grid" is ever sent to or stored by the server**, only the final flat array:

- **Single** — one seat at a clicked point.
- **Line** — `count` seats linearly interpolated between two endpoints.
- **Grid** — `rows × cols` seats, conventionally row-labeled (A1, A2, ... B1, B2, ...) as a *client-side label convenience only*, never interpreted as a row server-side.
- **Curve** — `count` seats along a quadratic Bézier curve (for a curved back row, balcony edge, etc.).

Each tool's output appends to one in-progress collection; only the final "save" persists the whole thing in one `POST /admin/seat-layouts/draft` call (`screen_id`, `name`, the full `seats` array). **There is no endpoint to add seats to an already-created draft** — by design, consistent with the Builder framing taken literally: the canvas builds the complete list client-side first, the server only ever receives a finished collection.

### Lifecycle

```
POST .../draft           → status: DRAFT  (unlocked)
POST .../draft/{id}/lock  → locked_by_user_id set, heartbeat starts
PATCH .../seats/{id}      → edit one seat, re-checks lock ownership + staleness on every call
PATCH .../seats           → bulk-edit several seats at once, same re-check
POST .../draft/{id}/publish → status: ACTIVE, lock cleared, immutable from here on
```

**The draft lock (§4.6) is pessimistic, not optimistic** — appropriate given how rarely two admins would realistically edit the same theatre's layout simultaneously, and far simpler to reason about than merge/conflict resolution. Mechanics: `locked_by_user_id` + `lock_acquired_at` + `lock_heartbeat_at` columns directly on `SEAT_LAYOUT`, no separate lock table. Acquiring is one atomic conditional `UPDATE` (free, stale, or already-yours all succeed; held-by-someone-else-and-fresh fails with their identity in the response) — so two concurrent acquire attempts can never both believe they won. Every edit re-validates ownership *and* staleness (heartbeat older than ~2 minutes) as part of the same `UPDATE`'s `WHERE` clause — never a separately-cached "do I still hold this" check that could go stale itself. A client heartbeats every 25s to keep the lock alive while actively editing; an explicit release endpoint exists for the common case of finishing early.

**`ACTIVE` layouts are immutable in place** — any change requires a brand-new draft plus republish, so a showtime mid-booking against the current `ACTIVE` layout is never invalidated out from under it. **Cloning** an `ACTIVE` layout (e.g. to reuse one screen's seating for another screen) creates a fresh `DRAFT` with fresh per-seat UUIDs but identical labels/positions/types — never reuses the source seats' own IDs.

### Materialization

When a showtime is scheduled against a screen, theatre service reads that screen's current `ACTIVE` layout's seats, computes each one's real `price = base_price × price_multiplier`, and calls booking's `POST /internal/showtimes/{id}/materialize-seats` — which is what actually creates the bookable `SHOWTIME_SEAT` rows (a separate table, in a separate database, owned by booking, not theatre). This is the seam between "the seat layout as authored" (theatre's `SEAT_TEMPLATE`, reusable across many showtimes on the same screen) and "the seats as bookable for one specific screening" (booking's `SHOWTIME_SEAT`, one full copy per showtime). Idempotent via an unconditional unique constraint (`showtime_id`, `seat_template_id`) — a retried materialize call is a clean per-row no-op, not a separately-derived hash key.

---

## 8. Tradeoffs

| Tradeoff | What's given up | Why it was accepted |
|---|---|---|
| Server-derived idempotency keys (hash of identity fields) instead of a client-supplied `Idempotency-Key` header | Two genuinely distinct entities that happen to share every identity-defining field collide into one row | The client carries zero key-generation/retry-tracking burden; this collision is considered acceptable given how identity-defining fields are chosen (title+duration+language for a movie, etc.) |
| Freeform seat layout (no row/column structure) | No free adjacency ("what's next to this seat") lookup — would need runtime x/y proximity math | Can represent any real theatre's actual physical shape, not just rectangular grids; adjacency wasn't a stated requirement (§16.2 if ever needed) |
| Redis lock keys hash-tagged on `showtime_id` (one showtime's seats always share a cluster slot) | Sacrifices intra-showtime key spreading across a real Redis Cluster | Required for the atomic multi-key Lua script's correctness on a real cluster (`CROSSSLOT` otherwise); a single shard's throughput is orders of magnitude beyond even a hot-showtime stampede's burst volume, so there's no realistic scenario needing more than one shard for one showtime anyway |
| Pessimistic draft lock instead of optimistic/version-based conflict resolution | A second admin is fully blocked, not offered a merge | Two admins editing the *same* theatre's layout at the *same* moment is rare enough that simplicity wins over building real merge/conflict UX |
| Sync-based shadow inventory for seatmap reads (§5.7) instead of a live call to the theatre's system on every read | A bounded staleness window — a seat taken on another channel can appear available here until the next sync | The seatmap is the system's highest-volume read; the *actual* lock-correctness check happens at `hold_seats()` time regardless, so staleness here only ever costs a clean, recoverable 409, never a double-booking |
| Outbox built for real this phase (table + relay worker) rather than deferred again | Real new infrastructure (one more table, one more worker) for a feature most of the time does nothing (mock theatre integration never actually fails) | The pattern had been referenced in the design doc for several phases without ever actually existing; the new failure-path tests needed genuine retry behavior to verify, not just a comment promising it exists later |
| `confirm_hold` synchronous-first, Outbox fallback-only (v21) — but `release_hold` stays purely async always | Confirm now waits on a real external call before returning (small added latency on the customer's "pay" click); the asymmetry means two superficially similar calls (`confirm_hold`/`release_hold`) are handled differently | A confirm is the one moment a real cross-channel double-booking risk exists if the theatre's own hold TTL fires before an async-only path got to it — closing that race is worth a small latency cost. A release isn't time-critical the same way (the seat just stays held slightly longer, never double-booked), so it doesn't carry that same cost |
| `MockTheatreIntegration` always succeeds locally; real per-POS-system adapters are future work (§16.8) | No current automated proof against a *real* theatre API's actual quirks | There's no real theatre API to integrate against yet; the Protocol seam is what makes adding one later a zero-orchestrator-change addition |
| No virtual queue for hot-showtime stampedes (§16.1) | A burst of demand produces fast clean 409s rather than smooth queued admission | Correctness (no double-booking) is already guaranteed regardless; a single Redis shard's throughput comfortably absorbs even the stated worst-case burst, so queueing is a UX nicety, not a correctness need, and was deferred |
| Draft seat-layout creation has no idempotency key | A genuine network retry during draft creation could (in theory) create a duplicate draft | A payload hash of `screen_id`+`name` isn't safe here — the same screen legitimately gets many new drafts reusing the same name over its lifetime; a dedup hit would silently resurrect a stale draft instead |
| `BOOKING.theatre_hold_id` added with no backfill for pre-existing rows | Old bookings have `NULL` and structurally cannot have their hold released via the new path | Acceptable specifically because those bookings predate the external-lock feature entirely — they were never given a real external hold to release in the first place |
| Routing service has wide-open CORS (`allow_origins=["*"]`) | No origin restriction at all at this layer | No auth exists yet either (`AUTH_ENABLED=false` everywhere until Phase 10) and nothing relies on cookies/credentials; a real API gateway replaces this entire service's slot in production with its own policy |
| `services/booking`'s layered structure (`domain`/`application`/`adapters`) vs the flatter `admin`/`customer`/`common` split used in catalog/theatre | Two different internal organizing principles across services | Booking has no admin/customer split to make (its routes are customer-facing + internal-only) — its actual axis of complexity is the saga/ports-and-adapters shape, which the domain/application/adapters split serves; catalog/theatre's complexity axis is "who's calling this," which the admin/customer split serves instead |

---

## 9. Databases and tables

One Postgres database per service (own container locally, one logical DB each), no cross-service foreign keys — cross-service references are plain UUID columns, intentionally unenforced (mitigated by soft-delete-never-cascades, snapshotting critical fields at write time, and validating against locally materialized data rather than a live call).

### `catalog_db`

| Table | Holds | Notes |
|---|---|---|
| `movie` | Title, description, duration, language, poster asset ref, `is_active` | Soft-delete only (`is_active`), never hard-deleted |
| `movie_release` | Per-(movie, city) release/end dates | `city_id` is a *loose* reference to theatre service's `CITY` — no FK, no local copy of city data itself |

### `theatre_db`

| Table | Holds | Notes |
|---|---|---|
| `city` | Name, state | Source of truth for cities; no admin endpoint creates them at this phase — seed-script-only |
| `theatre` | Name, address, real FK to `city` | |
| `screen` | Name, real FK to `theatre` | |
| `seat_layout` | Name, `status` (`DRAFT`/`ACTIVE`), draft-lock columns (`locked_by_user_id`, `lock_acquired_at`, `lock_heartbeat_at`), real FK to `screen` | One screen can have many layouts over time, but only ever one truly current `ACTIVE` one (not DB-enforced — a known, documented gap, §10) |
| `seat_template` | `label`, `position_x`/`position_y`, `seat_type`, `price_multiplier`, `is_active`, real FK to `seat_layout` | The *authored* seat shape — reusable across every showtime scheduled on that screen while this layout stays `ACTIVE` |
| `showtime` | `movie_id` (loose ref to catalog) + admin-supplied `movie_title` snapshot, real FK to `screen`, `start_time`, `base_price`, `is_high_demand`, `is_active` | Created `is_active=false`; a separate activate step flips it. No hard delete — deactivation only |

### `booking_db`

| Table | Holds | Notes |
|---|---|---|
| `showtime_seat` | Per-showtime copy of each seat (`label`, position, type, real computed `price`), `status` (`AVAILABLE`/`LOCKED`/`BOOKED`), `locked_by_booking_id`, `lock_expires_at` | The actual bookable inventory — one full row set *per showtime*, copied from `seat_template` at materialization time, not referenced live |
| `showtime_meta` | `movie_title`, `theatre_name`, `screen_name`, `start_time`, `base_price` per showtime | A local cache populated once at materialize time, specifically so the seatmap read and booking-creation paths never need a live cross-service call |
| `booking` | `idempotency_key`, `user_id`, `showtime_id`, snapshotted `movie_title`/`seat_labels`/`price_paid`, `status`, `expires_at`, `theatre_hold_id` | The booking record itself — snapshots everything it needs independent of the source records' current state |
| `pending_theatre_call` | `call_type` (`CONFIRM_HOLD`/`RELEASE_HOLD`), `booking_id`, `theatre_hold_id`, `status` (`PENDING`/`DONE`/`FAILED`), `attempts`, `next_attempt_at`, `last_error` | The Outbox — written in the same transaction as the booking event that triggered it, processed independently by the relay worker |

### `payment_db`

| Table | Holds | Notes |
|---|---|---|
| `payment` | `booking_id` (unique — itself the natural idempotency key), `amount`, `status` | Mocked: every payment is created with `status='SUCCESS'` |

### `user_db`

| Table | Holds | Notes |
|---|---|---|
| `app_user` | `email` (unique), `password_hash`, `role` (`CUSTOMER`/`ADMIN`) | Named `app_user`, not `user`, since `user` is a reserved Postgres word |

### `asset_db`

| Table | Holds | Notes |
|---|---|---|
| `asset` | `filename`, `content_type`, `byte_size`, `storage_path`, content-hash idempotency key | Local-CDN-mock-only — not part of the production ownership model; would be replaced by real object storage |

---

## 10. Assumptions

- **`AUTH_ENABLED=false` everywhere in local development** — every service's `require_role`/`get_auth_context` is a no-op until Phase 10 deliberately flips this. Lock-gated theatre endpoints fall back to a client-supplied `X-Admin-User-Id` header for caller identity in this mode.
- **At most one `ACTIVE` seat layout per screen** is assumed but *not* DB-enforced — publishing a new draft never deactivates a screen's prior `ACTIVE` layout. A pre-existing, explicitly documented gap (not fixed in any phase so far).
- **The theatre's own ticketing system enforces a TTL on `hold_seats()` comparable to `hold_duration_seconds`** — `confirm()`'s synchronous-first `confirm_hold` call (v21) removes most of the practical risk in the common case, but the assumption still matters for the Outbox *fallback* path (if the synchronous attempt itself fails) and for `release_hold` (always async) — both are partly defensive against this *not* being exactly true (clock skew, when their TTL clock actually started, etc.), rather than something this system could ever verify.
- **`MockTheatreIntegration` always succeeds** in local development and every pre-existing test — no real theatre POS API exists to integrate against yet.
- **Mocked payment always succeeds** — there's no real payment gateway behind `payment` service.
- **Clocks are roughly synchronized** across services and the database — several correctness arguments (sweep expiry, lock staleness, hold TTLs) lean on wall-clock comparisons being meaningful across process boundaries.
- **No real concurrent load/chaos testing yet** beyond the specific scripted concurrency tests per phase (§13's full failure table is the planned but not-yet-fully-executed verification surface, per `implementation-plan.md`'s Phase 11).
- **A "genuine retry" always re-derives the same idempotency key from the same payload** — assumes well-behaved retry semantics on the client side (same fields resubmitted, not a logically-different request that happens to collide).
- **City data needs no per-service copy beyond a loose `city_id` reference** — catalog and booking both reference theatre's `CITY` by ID only, assumed acceptable until a stated future phase (§13's noted "no local CITY copy until Phase 13").

---

## 11. Future enhancements

(Mirrors `docs/design.md` §16, listed here for completeness.)

- **Virtual queue** for hot-showtime stampedes (§16.1) — smooth queued admission instead of fast clean 409s, once/if that UX matters more than the throughput headroom already absorbing it.
- **Multi-event-type support** (§16.2) — concerts, sports, etc., beyond movies; would likely need the freeform seat-layout model's adjacency gap addressed too.
- **Full notification system** (§16.3) — booking confirmations, reminders; `EventPublisher`/`LoggingEventPublisher` is the seam already in place, currently a no-op pending a real event bus.
- **Event-driven seat release** (§16.4) — react to expiry/cancellation via the event bus rather than (or in addition to) the polling sweep worker.
- **Theatre-manager role** (§16.5) — a partial-admin scope, presumably theatre-scoped rather than global-admin.
- **Showtime cancellation workflow** (§16.6) — the larger "cancellation with consequences" question (refunds, notifying existing bookers) is explicitly out of scope today.
- **Real `TheatreIntegration` adapters** per POS system (Vista, Moviebook, proprietary systems, §16.8) — `THEATRE.integration_type` selecting the concrete adapter at runtime is designed for but not built; only the mock exists.
- **Real event bus** (Phase 13) — replacing `LoggingEventPublisher`; would also be the natural place to publish from the Outbox relay instead of (or alongside) direct API calls.
- **`AUTH_ENABLED=true` in production** (Phase 10) plus the full role × endpoint access-control test matrix.
- **Real API gateway** replacing the routing service's slot in production (§15), with its own CORS/auth/rate-limiting policy.
- **Real CDN + object storage** replacing local-cdn-mock.
- **Local `CITY` copy in catalog** (currently a loose `city_id` reference only) — noted as deferred until Phase 13.
- **Fixing the "at most one `ACTIVE` layout per screen" gap** — currently assumed, not enforced.
