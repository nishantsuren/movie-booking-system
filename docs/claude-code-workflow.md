# Working with Claude Code on this project

This is the handoff point: everything up to here (design doc, implementation
plan, Phase 0 scaffold) was built in this conversation. Phases 1 onward
happen in Claude Code, on your machine, where there's persistent file
access, a real Docker daemon, and git.

**Updated after Phase 1**: `docs/design.md` moved to v9 during that phase —
idempotency keys for create-type endpoints are now derived server-side as
a deterministic hash of each entity's identity-defining fields (§11.1),
replacing the original client-supplied `Idempotency-Key` header. That
change is reflected in the prompts below wherever it's relevant (Phases
2 and 3), and Phase 5 has explicit handling for the one case the design
doc itself defers — booking creation's natural identity includes a
seat-id list, which needs a canonicalization step the doc deliberately
didn't pre-decide.

**Updated after Phase 3**: `docs/design.md` moved to v10 during that
phase — two build-time corrections, not silently worked around but fed
back into the doc. (1) `SHOWTIME` now carries `base_price`, with
`SHOWTIME_SEAT.price` computed as `base_price * price_multiplier` at
materialization — the original schema had nothing for the multiplier to
multiply against. (2) Showtime "deletion" was redesigned entirely: there's
no hard delete anymore, just `is_active` defaulting `false` on creation,
a new `activate` endpoint, and `DELETE` flipping it back to `false` with
zero interaction with booking state. This removes a race condition Phase
5's prompt was originally written to test for — see the note inside that
prompt below.

**Updated after Phase 4**: `docs/design.md` moved to v11 — the original
§5.1/§5.2 contradicted each other on a real Redis Cluster (an atomic
multi-key `EVAL` only works if every key maps to the same hash slot, but
"omit hash tags so seats spread across nodes" guarantees they often
won't, which raises `CROSSSLOT` in production). Fixed by hash-tagging
lock keys on `showtime_id`: one showtime's seats now always colocate on
one slot (making the atomic script safe), while different showtimes still
land on different slots (cluster-wide load still distributes normally).
This reverses a specific instruction in the original Phase 4 prompt —
see the correction inside it below — and changes what Phase 12's load
test should actually verify.

**Updated after Phase 5**: `docs/design.md` moved to v12 — two gaps the
design doc had left open or missed entirely, both resolved during the
build. (1) The booking idempotency-key canonicalization deferred since
v9 is now concretely decided: a sha256 hash of
`showtime_id|user_id|sorted-seat-ids`, enforced via a **partial** unique
index scoped to live (`PENDING`/`CONFIRMED`) bookings only, not a plain
unique constraint — because the same user/showtime/seats triple is a
legitimate *recurring* identity once a hold expires, not a permanently
unique entity. (2) A gap nobody had flagged before the build: booking
service has no live path to catalog service, so it had no way to
populate `BOOKING.movie_title` from just a `movie_id`. Resolved by
extending admin-supplied trust (already established for `movie_id`) to
`movie_title` too — the admin form sends both, theatre passes it through
materialization, booking caches it locally. Zero new live cross-service
calls. This affects Phase 9's admin form, noted inside that prompt below.

**Updated after Phase 6**: `docs/design.md` moved to v13 — and this one
is worth reading carefully, because the gap traces back to a soft hedge
in this very file. §5.4's read-time reconciliation rule (a seat whose
hold has technically expired should be immediately bookable by someone
else, not stuck until the sweep worker physically gets to it) was always
stated in the design doc, but Phase 5's actual `select_seats`/`lock_seats`
queries only ever matched `status = 'AVAILABLE'` — never implementing the
`OR (status = 'LOCKED' AND lock_expires_at < now())` half of the rule.
The original Phase 6 prompt below asked Claude Code to handle this only
"if not already covered by Phase 5's seatmap-reading logic" — a
conditional phrasing that should have been an unconditional verification
step. Fixed during Phase 6's build by adding the correct predicate to
both queries (`postgres_seat_repository.py`). The corrected version below
no longer hedges and now requires an explicit regression test for this
predicate as part of Phase 6 itself — it belongs there, not in Phase 11,
since Phase 11's actual mandate is infra-failure chaos testing (killed
processes, network partitions) rather than general correctness
regressions; duplicating the test there would just be redundant.

**Updated after Phase 6, again**: `docs/design.md` moved to v14 — found
not by reading code but by trying to *write* §5.4's own required test 3
("race a concurrent payment-confirm against the sweep's selection of the
same booking, confirm always wins"). That test turned out to be
impossible to construct as originally specified: `confirm` had its own
wall-clock expiry check (because at Phase 5 build time, nothing else
ever would), and for the sweep to even select a booking as a candidate,
its `expires_at` must already be in the past — so `confirm`'s own check
always rejected the booking *before* any race with the sweep could
happen at all. Resolved (confirmed with the user before changing it):
`confirm` now drops its wall-clock checks entirely and gates purely on
state, making the sweep the sole wall-clock authority. Practical effect:
a customer confirming right at the 10-minute wire, just ahead of the
sweep's next poll, now completes their purchase instead of being
rejected over timing that nothing had actually acted on yet. This
retires Phase 5's `test_confirm_fails_after_hold_expires` outright — its
premise no longer holds — with the equivalent behavior now covered in
Phase 6's own suite. See the corrections inside both the Phase 5 and
Phase 6 prompts below, and Phase 8's customer-facing note, since this
changes what an "expired" countdown in the UI actually guarantees.

**Updated after Phase 7**: `docs/design.md` moved to v15 — two
Phase-7-specific build decisions worth knowing if you're touching user
service or building login/register UI later. (1) `USER` is implemented
as a table named `app_user`, not `user` — `user` is a reserved word in
Postgres and `CREATE TABLE user` is a syntax error, so this is the one
deliberate exception to the "table name matches entity name" convention
used everywhere else. (2) `POST /auth/register` deliberately does *not*
follow the general server-derived-idempotency pattern (conflict → return
the existing row). Email is the natural identity key, but password isn't
part of it, so a request that conflicts on email could be a genuine
retry or a different person hitting an already-taken email — indistinguishable
from the server's side, and returning the first registrant's row on
conflict would be a real account-confusion bug, not a harmless replay.
A conflict here is `409`, not `200`/`201` with existing data. See the
note inside Phase 10's prompt below.


**Updated after Phase 9**: `docs/design.md` moved to v17 during that
phase — six missing admin-facing list/get endpoints were added (§9 in
the design doc), a real constraint in §4.5 was surfaced (the canvas
builds its complete seat list client-side; `POST .../draft` is the only
path that ever inserts `SEAT_TEMPLATE` rows — there is no "add seats to
an existing draft" endpoint, by design), and two browser/infrastructure
issues were found and fixed: a Vite `base` option requirement for a SPA
served under a sub-path prefix (`/admin/`), and a trailing-slash routing
gap where `GET /admin` (no slash) silently served the wrong SPA. Also
flagged for a future pass: customer-web has the same latent dark-mode
contrast bug found and fixed in admin-web's `index.css` this phase.

**Aggregator architectural correction**: `docs/design.md` moved to v18
— the system was missing the external seat lock against the theatre's
own ticketing system. A Redis lock alone only protects against
concurrent BookMyShow users; it does nothing against a user booking the
same seat via the theatre's website, another aggregator, or a box-office
terminal simultaneously. Two-phase locking is now the correct model: (1)
Redis lock (within-platform, §5.1, unchanged) then (2) `TheatreIntegration`
external hold call (§5.7, new). A `MockTheatreIntegration` (always
succeeds) is wired for local dev and existing tests — no existing test
changes. A new Phase 9.5 builds this interface and wires it into the
booking saga. Phases 10–13 below are renumbered from their original
10–13 numbering; their content is otherwise unchanged except where the
TheatreIntegration failure modes affect them (noted in Phase 12, the
chaos/resilience phase).

**Updated after Phase 8**: `docs/design.md` moved to v16 — the customer
SPA was the first time this system was actually exercised through a real
browser instead of curl/integration tests, and it found things a
backend-only test suite structurally could not. Three gaps in this
category, all relevant to Phase 9 since it's also a browser SPA hitting
the same shared infrastructure:
- **Routing service had no CORS headers** — every API response was
  silently blocked by the browser, invisible to curl since curl doesn't
  enforce CORS at all. Fixed in the routing service itself.
- **The local CDN mock's static-file serving had no SPA fallback** — any
  direct navigation to a client-side route (e.g. a reload mid-flow) 404'd
  instead of serving `index.html` for the router to handle.
- **Vite's default build output directory (`assets/`) collided with the
  CDN mock's existing `GET /assets/{asset_id}` route** — bundle filenames
  got matched as attempted UUIDs and every JS/CSS request 422'd. Fixed by
  renaming Vite's output dir to `app-assets/` rather than touching the
  established `/assets` contract.

Separately, three customer-facing read endpoints turned out to exist
only on paper — Appendix A documented them, but nothing in Phases 1–7
had actually implemented them, because API-only testing never exercised
a real browse flow end to end: `GET /cities` (theatre — didn't exist at
all before this), `GET /showtimes/{id}` (theatre, plain), and two
existing endpoints got enriched rather than just implemented —
`GET /movies/{movie_id}/showtimes?city=&date=` now makes one
low-frequency live call to catalog per request (confirmed acceptable —
this is a browse-time path, not the booking hot path), and
`GET /showtimes/{id}/seatmap` now returns movie/theatre/screen/time/price
context sourced entirely from `SHOWTIME_META`, which §4.3 extends to also
carry `theatre_name`/`screen_name`/`start_time`/`base_price` alongside
the `movie_title` it already had (v12) — specifically so the system's
highest-volume read path (§2) still makes zero live cross-service calls.
Theatre service already owns all of those new fields internally via real
FKs, so this is pure backend wiring — it does not add any new field to
the admin showtime-creation form from Phase 9.

Finally: the post-countdown grace window question Phase 8's prompt
originally left as an open product decision (§5.6/v14 — confirm can
still succeed after the displayed countdown hits zero) is now resolved.
Confirmed with the user: the UI surfaces this explicitly rather than
leaving it as an unadvertised possibility, conditional on the
no-double-booking guarantee holding regardless of timing — which Phases
4 and 6 already prove under real concurrency, so the condition holds.

## One-time setup

```bash
cd movie-booking-system
git init
git add .
git commit -m "Phase 0: foundation scaffolding"
```

Then open this directory in Claude Code (`claude` in the terminal, from
inside the project root). It will read `CLAUDE.md` automatically at the
start of every session — that's what carries the project context forward
without you needing to re-paste the design doc into every prompt.

Before your first Phase 1 session, bring the stack up yourself and
confirm the Phase 0 exit criteria in `README.md` still pass on your
machine — you want a known-good baseline before Claude Code starts
changing things. In short: `docker compose up -d` for Postgres/Redis,
`./scripts/dev.sh` for the backend services (native, not containerized —
see `README.md` for why and the full verification steps).

## The general pattern, every phase

1. Start a fresh Claude Code session for each phase (`/clear` if
   continuing in the same terminal, or a new one). Fresh context per
   phase keeps Claude Code focused on the current boundary rather than
   carrying forward assumptions from whatever was just discussed.
2. Use the phase prompt below — every phase from 1 through 13 is fully
   written out and ready to paste as-is.
3. Let it implement, then **insist on seeing real test output**, not a
   description of what would happen. The implementation plan's
   verification criteria are specific for exactly this reason.
4. Review before committing — especially for Phases 4–6, the
   correctness-critical core. These are worth reading line by line, not
   just trusting because the tests passed; tests only catch what they're
   written to check.
5. Commit, update the "Current state" section of `CLAUDE.md`, move to the
   next phase.

If a phase reveals something genuinely wrong in `docs/design.md` (this
has already happened a few times during design and once during Phase 1
itself — it's normal, not a failure), have Claude Code propose the
specific edit, review it yourself, and commit the doc change alongside
the code change so they never drift apart.

---

## Phase 1 prompt (already implemented — kept here for reference)

```
We're implementing Phase 1 of docs/implementation-plan.md. Before writing
anything, read docs/design.md sections 3, 4.1–4.4, and Appendix A/C
(catalog + theatre endpoints), plus the Phase 1 entry in
docs/implementation-plan.md for exact scope and verification criteria.

Build:

1. Catalog service (services/catalog/): MOVIE and MOVIE_RELEASE tables
   per the §4.4 schema (release_date, planned_end_date, actual_end_date),
   customer endpoints (GET /movies, GET /movies/{id}) and admin endpoints
   (POST/PUT/DELETE movies, POST/PUT releases) per Appendix A/C. DELETE
   must be a genuine soft-delete (is_active flag) per §4.2 — there's
   nothing to cascade to yet at this phase, but build it correctly now so
   later phases don't need to revisit it.

2. Theatre service (services/theatre/): CITY, THEATRE, SCREEN tables —
   NOT SEAT_LAYOUT or SEAT_TEMPLATE, that's Phase 2, don't build it yet —
   and their customer/admin endpoints per Appendix A/C.

3. Local CDN mock (local-cdn-mock/): both route groups per §3.1 — static
   file serving for the SPA bundles (which don't exist yet, but the route
   should work) and the asset upload/serve endpoints (POST /assets,
   GET /assets/{id}) backed by an ASSET metadata table.

4. Wire AUTH_ENABLED-gated auth onto every admin endpoint in both
   services using shared/auth/auth.py's require_role("ADMIN") dependency
   — reuse it, don't reimplement it.

5. Use shared/idempotency/idempotency.py for mutating endpoints where it
   genuinely applies per §11.1 (endpoints a client might plausibly retry
   and that create a resource). Use your judgment on which ones need it
   at this phase — flag any you're unsure about rather than guessing.

For migrations, follow CLAUDE.md's convention: numbered SQL files applied
by a small script, tracked via a schema_migrations table, no ORM/migration
framework.

Update docker-compose.yml if the existing stub service definitions need
to change now that real logic exists.

Write infra/seed/seed.py (currently a placeholder) to idempotently
populate a small realistic dataset — a few cities, theatres, screens,
movies, and releases.

Then implement and run the integration tests from Phase 1's verification
criteria in docs/implementation-plan.md: full CRUD lifecycle via admin
endpoints, city-scoped customer browse, an asset upload/retrieve
round-trip, and the soft-delete-non-cascading test. Run them against the
actual docker-compose stack, not mocks, and show me the output.

If anything in the design doc is ambiguous or turns out impractical once
you're actually building it, stop and tell me rather than guessing.
```

---

## Phase 2 prompt (already implemented — kept here for reference)

```
We're implementing Phase 2 of docs/implementation-plan.md. Before writing
anything, read docs/design.md §4.5 (freeform seat layout authoring) and
§4.6 (draft edit lock), plus the seat-layout endpoints in Appendix C and
the Phase 2 entry in docs/implementation-plan.md.

Build, in theatre service (services/theatre/):

1. SEAT_LAYOUT and SEAT_TEMPLATE tables per the §4.5/§4.6 schema — there
   is NO row/column structure anywhere: SEAT_TEMPLATE is id,
   seat_layout_id, label (free text), position_x, position_y, seat_type,
   price_multiplier, is_active. SEAT_LAYOUT carries status (DRAFT|ACTIVE)
   plus the three lock columns: locked_by_user_id, lock_acquired_at,
   lock_heartbeat_at.

2. Draft lifecycle endpoints per Appendix C:
   - POST /admin/seat-layouts/draft — accepts a flat seats array, no
     server-side grouping or row inference of any kind.
   - PATCH .../seats/{seat_id} and PATCH .../seats (bulk) — both must
     verify the caller currently holds the lock, not merely that a lock
     exists on the layout (§4.6). Re-check ownership on every call — this
     is the part most likely to get built wrong as a one-time check at
     acquire time instead of an ongoing one.
   - POST .../publish — single transaction: flip to ACTIVE and assign to
     the screen atomically, no window where one happened without the
     other.
   - POST /admin/seat-layouts/{layout_id}/clone — fresh UUIDs per seat,
     same labels/positions/types, targeting a different screen.

3. The draft lock itself (§4.6):
   - POST .../lock — acquire if free or stale, or heartbeat-refresh if
     the caller already holds it. 409 with the current holder's info if
     held by someone else and not stale.
   - DELETE .../lock — explicit release.
   - Staleness: no fixed TTL. Treat the lock as reclaimable if
     lock_heartbeat_at is older than ~2 minutes — a read-time check at
     the moment a second admin requests it, not a background sweep.
     Deliberately don't build a sweep worker for this (that's Phase 6,
     and it's for a different mechanism) — a stale draft lock only
     matters at the moment someone else asks for it, so a sweep would be
     unnecessary infrastructure here.

4. Idempotency: figure out whether draft creation needs the
   server-derived idempotency-key mechanism established in Phase 1
   (§11.1 — a deterministic hash of identity-defining fields, not a
   client header). The design doc's worked examples (MOVIE,
   MOVIE_RELEASE, THEATRE, SCREEN, ASSET) don't cover seat-layout drafts
   directly. Propose the identity-defining fields you'd hash for this
   entity (e.g. screen_id plus something that disambiguates successive
   draft/publish cycles on the same screen) and flag it for confirmation
   rather than guessing silently — surface the gap, don't paper over it.

Then implement and run Phase 2's verification tests from
docs/implementation-plan.md: full layout lifecycle (create → edit →
publish, confirm single-transaction semantics), lock contention (second
session blocked with 409 + holder info), staleness reclaim (mock the
clock), lock-ownership-not-just-existence (a save attempt after going
stale must fail even though a lock record still technically exists), and
the clone test. Run them against the actual stack and show me the
output.

If anything is ambiguous, stop and ask rather than guessing.
```

---

## Phase 3 prompt (already implemented — kept here for reference; note that the actual build corrected two design-doc gaps, recorded as v10 — see the top of this file)

```
We're implementing Phase 3 of docs/implementation-plan.md. Before
writing anything, read docs/design.md §4.3 (materialization) and §5.3
(the uniqueness guard — read this carefully, an earlier design revision
had this wrong and the current version is the corrected one), plus the
showtime and internal materialize endpoints in Appendix C.

Build:

1. SHOWTIME table + admin CRUD in theatre service (POST/PUT/DELETE
   /admin/showtimes), per Appendix C.

2. Booking service stood up for the first time — but ONLY SHOWTIME_SEAT
   and the internal materialize endpoint
   (POST /internal/showtimes/{showtime_id}/materialize-seats). No
   locking, no BOOKING table, no payment — those are Phase 4/5. Don't
   get ahead of the phase boundary here even though it's tempting once
   booking service exists.

3. SHOWTIME_SEAT schema per §4.3/§5.3: id, showtime_id, seat_template_id
   (loose ref — this field matters, see point 5), label, position_x,
   position_y, seat_type, price, status, locked_by_booking_id,
   lock_expires_at.

4. Theatre service calls the materialize endpoint on showtime creation,
   using the standard §11.3 retry policy (bounded attempts, backoff). If
   materialization still fails after retries are exhausted, showtime
   creation itself must fail and return an error — fail closed, no
   orphan showtime with zero bookable seats. Verify this specific
   behavior with a test, not just the happy path.

5. The uniqueness guard: UNIQUE (showtime_id, seat_template_id) on
   SHOWTIME_SEAT, unconditional — NOT scoped to booking status, and NOT
   keyed on the display label. This constraint's job is preventing
   duplicate materialization (e.g. from a retried call), a different
   failure mode than the booking race itself (handled separately in
   Phase 5 by a PK-scoped conditional update). Don't conflate the two or
   key this off label.

6. Materialize endpoint idempotency: this one already has a natural,
   obvious key — (showtime_id, seat_template_id) per seat, exactly what
   the uniqueness constraint in point 5 already enforces. A retried
   materialize call for the same showtime should be a clean no-op via
   that constraint, not via a separately derived hash key — confirm
   that's how you've implemented it.

Then implement and run Phase 3's verification tests: materialize a
showtime against a published Phase 2 layout and confirm SHOWTIME_SEAT
rows match exactly; call materialize twice and confirm no duplicates;
stop booking service and confirm showtime creation fails closed after
retries; confirm the existing showtime-deletion guard still works (the
harder concurrent version of this test is Phase 5's, not this one). Run
them for real and show me the output.

If anything is ambiguous, stop and ask.
```

---

## Phase 4 prompt (already implemented — kept here for reference; note the hash-tag correction below, recorded as v11)

```
We're implementing Phase 4 of docs/implementation-plan.md — read
docs/design.md §5.1 (the Lua-script lock) and §5.2 (lock-key hash
tagging — read carefully, this was wrong in an earlier design revision
and corrected during this phase's actual build, see below) and §5.4
(TTL + read-time reconciliation) first.

This is deliberately the narrowest, highest-rigor phase in the whole
plan. Build ONLY the SeatLocker component (services/booking/adapters/) —
no HTTP endpoint, no BookingOrchestrator, no BOOKING table. It should be
a standalone class/module callable directly from tests:

- acquire(showtime_id, seat_ids, holder) — a single atomic Lua script
  (EVAL) that checks and sets all requested seat keys
  (lock:{<showtime_id>}:<seat_id>, SET NX EX 600) in one server-side
  round trip. All-or-nothing: if any seat is already held, none are set,
  and the conflicting seat IDs come back to the caller.
- release(showtime_id, seat_ids) — clears the given keys.
- Lock keys MUST use a Redis hash tag on showtime_id —
  lock:{<showtime_id>}:<seat_id>, where the {...} is the hash tag. An
  earlier version of this plan said the opposite (omit hash tags so
  seats spread across nodes), which is wrong on a real Redis Cluster: an
  atomic multi-key EVAL only works if every key it touches maps to the
  same hash slot, and without a hash tag a multi-seat lock attempt would
  intermittently span slots and raise CROSSSLOT. Hash-tagging on
  showtime_id keeps one showtime's seats together (atomicity-safe) while
  different showtimes still land on different slots (cluster-wide load
  still distributes normally) — §5.2 has the full reasoning, including
  why colocating one showtime's keys on a single shard is fine
  throughput-wise even under a stampede.

Required tests, run for real against Redis, not mocked:

1. Concurrency test — many genuinely parallel attempts (real threads or
   processes, not sequential calls in a loop dressed up as concurrent)
   to lock overlapping seat sets for the same showtime. Assert exactly
   the correct winners and zero partial-lock states.
2. TTL test — acquire a lock, wait past the 600s expiry (or fake the
   clock / use a short TTL just for this test), confirm a new attempt
   against the same seats succeeds with no manual cleanup.
3. Hash-slot sanity check — confirm all of one showtime's seat keys hash
   to the SAME Redis Cluster slot (this is what makes the atomic EVAL
   safe — verify it directly, don't just assume the hash tag syntax is
   correct), and confirm two different showtimes' keys hash to different
   slots.
4. Redis node-failure test — kill a Redis node mid-test, confirm client
   retry-with-backoff recovers once a replica promotes, with no special
   fallback path needed (§11.4 — single-node failure is handled by Redis
   Cluster itself plus client retry, nothing more).

Do not wire this into an HTTP API or start on BookingOrchestrator — that
is explicitly Phase 5's scope, not this one. Show me the actual
concurrency test output (pass/fail counts, not just "it passed") before
calling this phase done.

If anything is ambiguous, stop and ask.
```

---

## Phase 5 prompt (already implemented — kept here for reference; the idempotency canonicalization and a movie_title gap discovered mid-build were both resolved, recorded as v12)

```
We're implementing Phase 5 of docs/implementation-plan.md — read
docs/design.md §5.3 (the conditional-update correctness guarantee), §7/§8
(the BookingOrchestrator code sample), §11.1 (idempotency — read this one
carefully, see point 6 below), and the booking + payment endpoints in
Appendix A first.

Build:

1. BOOKING table per the §4 ERD: id, user_id, showtime_id,
   idempotency_key, movie_title, seat_labels, price_paid, status,
   expires_at — the denormalized snapshot fields are populated at
   creation time, not derived later.

2. POST /bookings — wires Phase 4's SeatLocker into a BookingOrchestrator
   (follow the §7/§8 code sample's shape: select_seats() acquires the
   lock then creates a PENDING booking; confirm() does the conditional
   update + lock release + event publish). On lock conflict, return a
   clean 409 with the conflicting seat IDs.

3. Payment service (mocked) — POST /payments always succeeds, per
   Appendix A.

4. POST /bookings/{id}/confirm — the actual correctness guarantee is the
   conditional UPDATE keyed on SHOWTIME_SEAT's primary key (§5.3):
   `WHERE id = :seat_id AND status = 'LOCKED' AND locked_by_booking_id =
   :booking_id`. This is what makes a double-booking structurally
   impossible regardless of what happened upstream — test the conflict
   path explicitly (two confirms racing for the same seat), not just the
   happy path.

5. DELETE /bookings/{id} — explicit cancel, PENDING bookings only.

6. Idempotency — read this carefully, it's explicitly unresolved in the
   design doc and this phase has to resolve it, not assume an answer.
   §11.1 (v9) established server-derived idempotency keys (a deterministic
   hash of identity-defining fields) for catalog/theatre/asset creation in
   Phase 1, but explicitly defers booking creation from that approach,
   because BOOKING's natural identity includes a seat-id list, which needs
   a canonicalization step (e.g. sort the seat IDs, then join them) before
   the same hashing approach applies cleanly. Decide and implement the
   concrete canonicalization now — propose your approach (what fields go
   into the hash, in what order, how seat_ids get canonicalized) before or
   as part of building this, and update docs/design.md §11.1 to record the
   decision once made, so it stops being an open question there. Note:
   plain `UNIQUE` is the wrong constraint shape here even once you've
   picked a hash formula — unlike MOVIE/THEATRE/etc., the same
   (user, showtime, seats) triple is a legitimate *recurring* identity (a
   hold expires, the same user retries the same seats later), so whatever
   constraint enforces this needs to stop applying once a booking reaches
   a terminal state, not dedupe against it forever.

7. A gap you may discover once you're actually wiring this up that the
   design doc doesn't flag anywhere: booking service has no live path to
   catalog service (by design — no cross-service calls on the booking hot
   path), so there's no way to populate BOOKING.movie_title from just a
   movie_id at booking-creation time. If you hit this, the fix is to
   extend the same admin-supplied-trust pattern §4.2 already uses for
   movie_id to movie_title as well — propose this explicitly (which
   endpoint needs the new field, how it flows through materialization to
   become available in booking service) rather than inventing a live
   lookup or leaving movie_title empty, and update docs/design.md to
   record it.

Then implement and run Phase 5's verification tests: happy-path
(browse → select → PENDING → pay → confirm → BOOKED, snapshot fields
correct, including movie_title); conflict test (two attempts, one 409);
expired-lock test (mock time past 10 minutes, confirm fails
appropriately); idempotency test (replay a request with the same derived
key while the original booking is still PENDING, confirm no
re-execution, then confirm a *new* request with the same seats succeeds
once the original reaches a terminal state — this second half is the
part a plain unique constraint would get wrong); cancel test (DELETE on
a PENDING booking releases the lock and reverts seats to AVAILABLE;
confirm it's rejected for a non-PENDING booking); and payment-down test
(circuit breaker trips, booking stays PENDING). Run them for real and
show me the output.

Note on scope that changed since this plan was first written: an earlier
version of this phase also called for a "showtime-deletion-race" test —
deleting a showtime concurrently with an in-flight booking against it.
That's no longer applicable. Showtime "deletion" (§4.3, design doc v10)
was redesigned during Phase 3 into a deactivation flag flip
(`is_active = false`) with zero interaction with `SHOWTIME_SEAT` or
`BOOKING` state — there's no row removal and no check-then-act race left
to guard against. Don't write a test for this; if you go looking for a
synchronous booking check to stress-test here, it doesn't exist anymore
by design, not by oversight.

If anything is ambiguous, stop and ask — especially the idempotency-key
canonicalization, since the design doc deliberately left it for this
phase to decide rather than specifying it upfront.
```

One more thing worth knowing about this phase in hindsight: the seat
availability queries built here (`select_seats`'s check, `lock_seats`'s
conditional `UPDATE`) turned out to only match `status = 'AVAILABLE'`,
missing the `OR (status = 'LOCKED' AND lock_expires_at < now())` half of
§5.4's read-time reconciliation rule. Not caught until Phase 6 — see the
note in that prompt below (v13). If you're using this prompt as a
reference for similar future work, make read-time-reconciliation
predicates an explicit, verified requirement wherever they apply, not an
assumption.

A second thing, also only caught later (v14): the "expired-lock test"
this prompt asked for — confirm fails once 10 minutes have passed — is
no longer how the system actually behaves, and the test itself is
retired. `confirm` originally self-policed wall-clock expiry because, at
the time this phase was built, nothing else did. Once Phase 6's sweep
worker existed, that made it the system's *only* legitimate wall-clock
authority — so `confirm`'s own clock check was removed entirely (Phase 6,
v14), and a confirm landing right at the wire, just ahead of the sweep's
next pass, now succeeds rather than being rejected over timing nothing
had actually acted on. If you're treating this prompt as a template: a
state-gated conditional update (§5.3) is the durable correctness
primitive here, never a clock check duplicated in two places — only one
component should ever own wall-clock authority for a given piece of
state.

---

## Phase 6 prompt (already implemented — kept here for reference; point 3 below is corrected from the original, which had a soft hedge that let a real bug slip through Phase 5, recorded as v13)

```
We're implementing Phase 6 of docs/implementation-plan.md — read
docs/design.md §5.4 in full first; it already specifies the exact SQL
and the exact test list this phase needs, so don't improvise a different
design.

Build the sweep worker (services/booking/adapters/) exactly per §5.4:

1. Single active instance via a Postgres advisory lock
   (pg_try_advisory_lock) on a dedicated, non-pooled connection — deploy
   with N replicas for redundancy, but only one is ever actively
   sweeping. Standbys retry the advisory lock on a poll interval (e.g.
   every 5s) and take over automatically if the active instance's
   connection drops.

2. The sweep query, on a separate (pooled) connection, every 15–30
   seconds:
   ```sql
   SELECT id, showtime_id FROM booking
   WHERE status = 'PENDING' AND expires_at < now()
   ORDER BY expires_at LIMIT 500;
   UPDATE booking SET status = 'EXPIRED' WHERE id = ANY(:ids);
   UPDATE showtime_seat SET status = 'AVAILABLE', locked_by_booking_id = NULL,
     lock_expires_at = NULL
   WHERE locked_by_booking_id = ANY(:ids) AND status = 'LOCKED';
   ```
   Both updates in one transaction — no window where a booking is
   EXPIRED but its seats are still LOCKED.

3. Read-time reconciliation — do NOT assume Phase 5 already implemented
   this correctly; verify it directly, because it didn't. Open
   postgres_seat_repository.py (or wherever Phase 5 put the
   availability-check and lock-acquisition queries) and confirm both use
   the full predicate: `status = 'AVAILABLE' OR (status = 'LOCKED' AND
   lock_expires_at < now())` — not just `status = 'AVAILABLE'`. If you
   find the narrower predicate, that's the bug: it means a seat whose
   hold has technically expired stays unbookable until this sweep worker
   physically processes it, which contradicts §5.4's stated rule that
   read-time reconciliation should make it immediately available. Fix it
   in both queries before moving on, and add a regression test:
   create a booking, let its lock expire (mock time, don't actually wait
   600s), do NOT run the sweep yet, then confirm a second, different
   booking attempt for the same seat succeeds immediately. If that test
   fails, the predicate fix didn't take in both places.

Required tests — the design doc's list, use it verbatim, don't write a
different one:
1. Kill the active instance mid-batch, confirm a standby takes over
   within one poll interval, no half-processed booking.
2. Start three replicas simultaneously, confirm exactly one acquires the
   advisory lock.
3. Race a concurrent payment-confirm against the sweep's selection of
   the same booking — confirm always wins if it reaches the database
   first. Before you can write this test at all, check whether `confirm`
   (built in Phase 5) has its own wall-clock expiry check — `WHERE ...
   AND lock_expires_at > now()` in the seat update, or an
   `is_expired()`-style check on the booking. If it does, this test is
   impossible to construct as stated: for the sweep to even select a
   booking as a candidate, its `expires_at` must already be in the past,
   so confirm's own clock check would reject it before any race with the
   sweep could happen at all. If you hit this, the fix is to remove
   confirm's wall-clock checks entirely and gate purely on state
   (`status = 'PENDING'`/`'LOCKED'`) — the sweep becomes the system's
   sole wall-clock authority once it exists. Confirm this change with me
   before making it, since it changes real behavior (a confirm landing
   right at the 10-minute wire, just ahead of the sweep's next pass, now
   succeeds instead of being rejected). Once fixed, verify both halves:
   confirm succeeds for a not-yet-swept expired booking if it beats the
   sweep there, and confirm correctly fails (sees `EXPIRED`) once the
   sweep has already run — these replace Phase 5's now-retired
   `test_confirm_fails_after_hold_expires`, which assumed nothing else
   would ever expire a booking, no longer true now that this worker
   exists.
4. Re-run the sweep immediately after a successful pass, confirm zero
   rows affected.
5. Seed a backlog of expired PENDING bookings, confirm it drains in
   bounded batches without degrading the booking API's normal request
   path.
6. The read-time reconciliation regression test described in the Build
   section's point 3 above (not this list's point 3, which is the
   confirm/sweep race) — this is required, not optional, given it was
   actually missing for an entire phase before this one.

Run all six for real and show me the output before calling this done.

If anything is ambiguous, stop and ask.
```

---

## Phase 7 prompt (already implemented — kept here for reference; two build-time decisions recorded as v15, see notes below and at the end)

```
We're implementing Phase 7 of docs/implementation-plan.md — read
docs/design.md §3 (the role claim) and the user-service endpoints in
Appendix A first.

Build:

1. USER table with role (CUSTOMER|ADMIN). Name the table app_user, not
   user — user is a reserved word in Postgres and CREATE TABLE user is a
   syntax error outright. This is the one deliberate exception to every
   other service's "table name matches entity name" convention; don't
   try to work around it with quoting tricks, just use a different name.
2. POST /auth/register, POST /auth/login — login issues a JWT including
   the role claim, using shared/auth/auth.py's existing JWT_SECRET /
   JWT_ALGORITHM conventions from Phase 0 — don't introduce a second,
   different auth scheme.
3. GET /users/{user_id}.

Idempotency note for POST /auth/register specifically — this is a
deliberate exception to the general server-derived-idempotency pattern
used everywhere else (§11.1), not an oversight: email is the natural
identity-defining field here, but password isn't part of that key, so a
conflict on email can't be distinguished between "this is a genuine
retry" and "a different person is targeting an already-taken email."
Returning the first registrant's row on conflict (the usual pattern)
would be a real account-confusion bug, not a harmless replay. Make a
conflict here return 409, not 200/201 with the existing row.

Leave AUTH_ENABLED=false everywhere else at this point — this phase
establishes the capability without flipping the switch that makes
everything else depend on it. That's Phase 10, deliberately separated so
"does auth work" and "does enforcing auth break something else" are
different, independently diagnosable questions.

Then: a register/login round-trip test confirming a valid JWT with the
correct role claim comes back, plus a test confirming a second
registration attempt with the same email gets 409, not a silently
"successful" replay. Then run the FULL existing test suite from
Phases 1–6 as a regression check — confirm AUTH_ENABLED=false still
bypasses cleanly everywhere and nothing built so far has accidentally
started requiring a token. Show me that regression run's output
specifically, not just the new tests' — a clean pass here is most of this
phase's actual value.

If anything is ambiguous, stop and ask.
```

---

## Phase 8 prompt (already implemented — kept here for reference; this is the phase that found three real endpoint gaps and three infrastructure gaps no API-only test could have caught, recorded as v16 — see Phase 9's notes for what carries forward)

```
We're implementing Phase 8 of docs/implementation-plan.md — read
docs/design.md §3 (architecture, including how service base URLs must be
environment-configured, §3.2) first.

Before writing any frontend code: confirm the framework choice with
me — this is the first phase introducing real frontend tooling and it's
worth deciding deliberately rather than defaulting to whatever's
familiar.

Once confirmed, build the customer SPA (apps/customer-web/):
- Browse movies → showtimes → seatmap → seat selection → booking →
  mocked payment → confirmation, calling through the routing service the
  whole way (never directly to a backend service), loading its own
  bundle and any images from the local CDN mock.
- The routing-service base URL must come from environment config, not a
  hardcoded localhost port — this is what lets the same build swap to a
  production API gateway later with zero code changes.
- Don't assume every endpoint this flow needs already exists and works
  just because it's documented in Appendix A — Phases 1–7 were built and
  tested via direct API calls, which never exercised a full real browse
  flow end to end. Test each call as you wire it up, not just at the end.

Then build an E2E test suite (Playwright or Cypress — pick one and say
which) covering: the full happy path against the real backend from
Phases 1–7; a seat-conflict scenario (two tabs/sessions racing for the
same seat); and a payment failure — confirm each error state surfaces
clearly in the UI rather than failing silently.

One scenario needs care, not a generic "countdown reaches zero, confirm
fails" test: as of Phase 6 (v14), the booking hold's 10-minute countdown
reaching zero in the UI does NOT mean confirm is now guaranteed to fail
— confirm only fails once the sweep worker has actually run and reclaimed
the seat (every 15–30 seconds), so a payment landing just past the
nominal deadline can still legitimately succeed. Test both real
outcomes — confirm succeeding just past the countdown (don't treat this
as a bug) and confirm failing once enough time has passed that the sweep
has genuinely run — rather than asserting the seat is unavailable the
instant the displayed countdown hits zero. Surface this explicitly in
the UI (e.g. "may still work for a few more seconds") rather than
leaving it as an unadvertised possibility — confirmed with me as the
right call, conditional on the no-double-booking guarantee holding
regardless of timing, which Phases 4 and 6 already prove under real
concurrency.

Run the suite for real and show me the output.

If anything is ambiguous, stop and ask.
```

---

## Phase 9 prompt (already implemented — kept here for reference; see top of file for the v17 admin-SPA build-time findings)

```
We're implementing Phase 9 of docs/implementation-plan.md — read
docs/design.md §4.5 (the freeform seat-layout authoring tools) and §4.6
(the draft lock) carefully first; this phase's centerpiece is built
directly on those.

Before writing any frontend code: this app shares infrastructure with
Phase 8's customer SPA (same routing service, same local CDN mock), and
Phase 8 found three infrastructure gaps the hard way that this phase
must not silently reintroduce. Verify each explicitly rather than
assuming Phase 8's fix automatically covers this app too:
- CORS — confirm the routing service's CORS policy actually covers
  whatever origin/port this app serves from, not just customer-web's.
  If the Phase 8 fix hardcoded a single allowed origin instead of a
  general policy, this app will hit the exact same silent-block problem
  Phase 8 did, and curl-based testing won't catch it either — test with
  a real browser.
- SPA fallback — confirm the local CDN mock's static-file serving falls
  back to this app's `index.html` for direct navigation to a client-side
  route (e.g. a reload mid-flow) under its own path prefix (`/admin/`),
  not just customer-web's path (`/`).
- Build output directory — if this app is also built with Vite (confirm
  the framework choice, same as Phase 8 — reusing Vite/React/TypeScript
  for consistency is reasonable but not assumed), its default build
  output directory (`assets/`) will collide with the CDN mock's
  `GET /assets/{asset_id}` route exactly the way customer-web's did.
  Rename it (e.g. `app-assets/`, matching the convention Phase 8 already
  established) rather than rediscovering this from scratch.

`GET /cities` (theatre service, added in Phase 8 — v16) is available now
for any city-scoped picker this app needs, e.g. a city dropdown on the
theatre-creation form. Don't build a separate ad hoc way to enumerate
cities.

One thing that does NOT require a new admin-form field: `SHOWTIME_META`
(booking service) was extended in Phase 8 to also cache
theatre_name/screen_name/start_time/base_price alongside movie_title.
Theatre service already owns all of those internally via real FKs
(screen → theatre, the showtime's own start_time and base_price) — this
is backend wiring within theatre/booking, not something the admin form
needs to newly supply.

Treat this as two sub-efforts, and consider doing the canvas editor as
its own focused session separate from the simpler CRUD work:

1. Standard CRUD forms: movies, theatres, screens, showtimes — admin
   forms over the Phase 1/3/5 admin endpoints. Comparatively
   straightforward, build this first. Three showtime-specific notes worth
   getting right rather than defaulting to generic CRUD-form patterns:
   the creation form needs a base_price field (§4.3, design doc v10 —
   this is what SHOWTIME_SEAT.price is computed from at materialization
   time, base_price * price_multiplier); the creation form must also send
   movie_title alongside movie_id (§4.3, design doc v12) — booking service
   has no live path to catalog service, so it relies entirely on what the
   admin form submits at creation time, which in practice just means
   carrying through the title already visible in whatever movie-selection
   dropdown the admin used to pick movie_id, not a separate lookup; and
   the action behind DELETE /admin/showtimes/{id} should be labeled
   "Deactivate," not "Delete," in the UI — that endpoint only flips
   is_active to false now, it doesn't remove anything, and the button
   copy should say what it actually does rather than implying a removal
   that doesn't happen. A separate "Activate" action calling
   POST .../activate is needed too.

2. The seat-layout canvas editor (apps/admin-web/) — the hardest UI
   surface in the whole project:
   - Line, grid, curve, and single-seat placement tools, each producing
     entries in a flat client-side seat list — none of these tools or
     any row/grouping concept is ever sent to the server, only the
     resulting flat list (§4.5).
   - Multi-select (rubber-band or shift-click) for bulk-editing
     type/price/active-status across a placed cluster.
   - Draft-lock UX (§4.6): show current lock status, block edits with a
     clear message (including who holds it) when another admin holds the
     lock, and surface staleness so an admin isn't confused about why
     they suddenly can or can't edit.

Then build an E2E test that authors a complete, realistic layout (150+
seats, mixing placement tools) through the canvas end-to-end, publishes
it, and confirms it renders correctly in the customer app's seatmap from
Phase 8 — this cross-app check is the strongest available integration
test between the two frontends. Also test the concurrent-edit case: two
simulated admin sessions against the same draft, confirm the second is
blocked with a clear message and can proceed once the first releases or
goes stale.

Run both for real and show me the output.

If anything is ambiguous, stop and ask.
```

---


---

## Phase 9.5 prompt — TheatreIntegration: the aggregator lock

```
We're implementing Phase 9.5, which was added to the build order after
Phase 9 based on an architectural gap identified in the original design.
Read docs/design.md §5.7 in full before writing anything — it defines
the interface, the two-phase lock sequence, the saga compensation logic,
the sync-based shadow inventory approach, and the mock implementation
requirements. Also re-read §5.1 (existing Redis lock) and §5.6
(confirm/cancel mechanics) so you understand exactly where this phase's
changes slot into code that already exists.

The scope is deliberately bounded: interface, mock, wiring, and failure
tests only. No real theatre POS API integration — that's §16.8 and
production work, not this phase.

Build:

1. The `TheatreIntegration` Protocol exactly as defined in §5.7:
   - `hold_seats(showtime_id, seat_ids, hold_duration_seconds) → HoldResult`
   - `confirm_hold(theatre_hold_id) → None`
   - `release_hold(theatre_hold_id) → None`
   - `sync_availability(showtime_id) → list[SeatStatus]`
   Place it in `services/booking/domain/` alongside the other Protocols —
   no framework imports there.

2. `MockTheatreIntegration` in `services/booking/adapters/`: always
   returns success for hold_seats (generating a random UUID as the
   theatre_hold_id), no-ops for confirm_hold/release_hold,
   returns current SHOWTIME_SEAT status for sync_availability.

3. Schema migration: add `theatre_hold_id` (nullable string) to the
   `BOOKING` table. Migration file per CLAUDE.md's numbered SQL
   convention. NULL default, no backfill — pre-existing rows stay null
   and §5.7's cancel/sweep logic explicitly skips the release_hold call
   for null theatre_hold_id.

4. Wire `MockTheatreIntegration` into `BookingOrchestrator` exactly per
   §5.7's updated code sample in §8 — two new steps in select_seats
   (after Redis lock: call hold_seats; on failure, compensate the Redis
   lock and raise), and confirm_hold written to the Outbox in the same
   transaction as the booking confirm. Inject it via the same
   constructor-DI pattern as `SeatLocker`, `PaymentClient`, etc.

5. Add `release_hold` to the sweep worker's expiry path (§5.4) and to
   the cancel endpoint (§5.6) — both must call it when they release a
   booking, skipping gracefully if `theatre_hold_id` is null.

6. Stand up the availability sync job in
   `services/booking/adapters/theatre_availability_sync.py` — same
   structural pattern as the sweep worker (Postgres advisory lock for
   single-active-instance, N replicas for redundancy). Configurable
   interval per showtime (e.g. 60s for active, slower for future). Uses
   MockTheatreIntegration.sync_availability in local dev.

Run the full existing test suite — confirm zero regressions. Then write
and run tests for the new failure paths specifically:
a. hold_seats returns a conflict — verify Redis lock is released, 409
   is returned with the correct conflicting seat IDs.
b. hold_seats times out / returns 5xx — verify Redis lock is released,
   503 is returned, circuit breaker trips after repeated failures.
c. confirm_hold fails after a successful hold and payment — verify the
   Outbox correctly retries it independently without affecting the
   booking's CONFIRMED status.
d. release_hold fails on sweep/cancel — verify the Outbox retries it,
   and the booking/seat state in BookMyShow's own DB is still correctly
   EXPIRED/CANCELLED regardless.
e. theatre_hold_id is null on a confirm/cancel — verify the call is
   skipped cleanly (pre-v18 booking compatibility, §5.7).

Show me the regression-pass output and all new failure-path test results
before calling this phase done. If anything in §5.7 is ambiguous once
you're building against it, stop and ask.
```

## Phase 10 prompt

```
We're implementing Phase 10 of docs/implementation-plan.md — read
docs/design.md §3.2 and §15 first.

1. Flip AUTH_ENABLED=true across every backend service's environment
   configuration.

2. Build a complete role × endpoint access-control matrix as an actual
   automated test suite — every endpoint across every service, crossed
   with every role (CUSTOMER, ADMIN, and unauthenticated/no token) →
   expected allow/deny. This needs to be genuinely exhaustive, not a
   manual checklist or a handful of spot-checks.

3. Re-run the full Phase 8 and Phase 9 E2E suites against the
   now-authenticated backend, confirming both apps still work end-to-end
   — this is the main thing this phase is actually checking: that turning
   auth on doesn't silently break a flow that was only ever tested
   without it.

4. Specifically confirm admin-only endpoints (Appendix C) reject valid
   but non-admin tokens with 403, not just reject missing tokens with
   401 — these are different failure modes and both need their own test.

5. If this phase is also where customer/admin login UI gets built (check
   whether Phase 8/9 already covered it — if not, it has to happen here,
   since the E2E re-run in point 3 needs real accounts to authenticate
   as): remember POST /auth/register is a deliberate exception to the
   general idempotency pattern (§11.1, design doc v15) — a duplicate
   email returns 409, not the existing user's data. Whatever UI calls
   this endpoint needs to handle that distinctly (e.g. "this email is
   already registered, try logging in"), not treat a 409 as a generic
   error or, worse, as a successful replay.

Show me the access-control matrix test output and the re-run E2E results
before calling this phase done. The bar here: the system should behave
identically from a functional standpoint to how it did with
AUTH_ENABLED=false, except that unauthorized requests are now correctly
rejected — nothing else should change.

If anything is ambiguous, stop and ask.
```

---

## Phase 11 prompt

```
We're implementing Phase 11 of docs/implementation-plan.md — read
docs/design.md §13 (the full failure table) first.

Before writing any new tests: go through §13 row by row and identify
which ones are already covered by tests from earlier phases (sweep-worker
failover — Phase 6; materialization retry — Phase 3; draft-lock
staleness — Phase 2; the booking conflict path — Phase 5, etc.) and which
are NOT yet covered by anything. Show me that classification first —
don't just start writing tests for everything in the table, since
re-testing what's already covered wastes effort and this phase is
explicitly about filling the remaining gaps, not duplicating work.

For the genuinely uncovered rows (likely: Postgres primary failover,
Redis full-cluster outage, a booking-service instance crashing
mid-request, and the duplicate-request/idempotency guarantee holding
under real concurrent retries rather than sequential test cases), write a
scripted chaos test per row — e.g. killing a Postgres primary
mid-transaction, severing booking service's network path to Redis
entirely — with an explicit assertion on the expected
degraded-but-correct behavior named in that row, not just "the system
didn't crash."

One more scenario worth adding even though it isn't a named §13 row:
Phase 8 (design doc v16) introduced the system's one deliberate live
cross-service call — theatre service calling catalog directly on
`GET /movies/{movie_id}/showtimes?city=&date=`, accepted specifically
because it's a browse-time path, not the booking hot path. That
exception deserves its own chaos test precisely because it's the only
place in the system where this risk exists at all: stop catalog service
and confirm this specific endpoint degrades reasonably (clear error or
timeout, not a hang) rather than taking theatre service down with it —
check whether a timeout/circuit-breaker was actually built for this call
or whether it was only ever exercised against a healthy catalog service.

The TheatreIntegration failure modes from Phase 9.5 (hold conflict,
theatre API timeout, confirm-hold retry, release-hold retry, null
theatre_hold_id skip) were already tested in Phase 9.5 — cross-check
§13's new rows against those results rather than re-running them here.
Add any remaining gaps: specifically, a full-cluster theatre-API outage
scenario where every hold attempt fails, and confirm the circuit breaker
trips and fails fast rather than holding Redis locks for thousands of
users against a permanently-down external system.

Run every chaos test for real and show me the output. The exit bar: every
claim in §13 has a test proving it, not just a sentence asserting it,
plus both additional scenarios noted above.

If anything is ambiguous, stop and ask.
```

---

## Phase 12 prompt

```
We're implementing Phase 12 of docs/implementation-plan.md — read
docs/design.md §2 (the capacity figures) and §5.2 (Redis key
hash-tagging — read carefully, the claim this phase needs to verify
changed during Phase 4's build, see point 2 below) first.

Before writing any scripts: confirm the load-testing tool with me — k6 or
Locust, your call, just say which and why.

Then build:

1. Sustained-load scripts hitting the §2 figures for catalog/showtime
   browsing, seatmap views, and booking creation. Confirm latency stays
   within reasonable bounds and that the Phase 6 reconciliation worker's
   sweep lag stays bounded under realistic booking-creation volume —
   instrument and report this specifically, don't just report request
   latency. One endpoint deserves its own latency line in the report
   rather than being averaged into general browsing numbers:
   `GET /movies/{movie_id}/showtimes?city=&date=` makes a live call to
   catalog service (Phase 8, v16) — the only live cross-service call
   anywhere in the system. Measure its latency and error rate separately
   under sustained browsing load, since it's the one place a slow
   dependency could leak into an otherwise-isolated service's response
   times.

2. A scripted hot-showtime stampede test: many concurrent requests
   against one showtime's full seat inventory (~150–300 seats), well
   beyond the Phase 4 unit-level concurrency test's scale. Confirm zero
   double-bookings under real concurrent load. For the Redis-distribution
   claim specifically — this is NOT "does one showtime's seats spread
   across nodes," that was the original (wrong, pre-Phase-4-build) plan
   and §5.2 now says the opposite on purpose: confirm instead that (a)
   one showtime's full set of lock keys all hash to the SAME Redis
   Cluster slot, which is what makes the atomic multi-key EVAL safe in
   the first place, (b) different showtimes' keys land on different
   slots, so overall cluster load still distributes normally across
   showtimes, and (c) the single shard holding the hot showtime's keys
   comfortably absorbs the full stampede burst without becoming a
   bottleneck — this is the actual throughput claim §5.2 makes
   ("a single shard's sequential throughput is orders of magnitude
   beyond even a hot-showtime stampede's burst volume"), and it's
   load-testable: measure that one shard's ops/sec under the stampede and
   confirm it's nowhere near that shard's ceiling.

3. A data-volume sanity check: seed a proportionally-scaled dataset
   (doesn't need to hit the full 45M-rows/day figure from §10, but enough
   to be meaningful) and confirm the partitioning strategy from §12 keeps
   query performance acceptable as SHOWTIME_SEAT grows.

Run all three and show me the actual numbers — latencies, throughput,
sweep lag under load, double-booking count (should be zero), confirmed
single-slot colocation for one showtime plus the measured headroom on
that shard under load. If reality doesn't match the §2 planning figures
or §5.2's throughput claim, say so explicitly and propose the doc
correction rather than quietly noting the discrepancy.

If anything is ambiguous, stop and ask.
```

---

## Phase 13 prompt

```
We're implementing Phase 13 of docs/implementation-plan.md — read
docs/design.md §15 first.

These items are largely independent of each other — feel free to tackle
them in a different order than listed, or flag which ones you think are
worth splitting into separate sessions given how different in kind they
are:

1. Real API gateway taking over the routing service's slot (§3.2) —
   centralized auth, rate limiting, WAF, TLS termination. Verify it
   actually enforces what the routing service didn't (try an unauthed
   request, a rate-limit-exceeding burst, confirm both are now rejected
   at the gateway).

2. Real event bus replacing the no-op EventPublisher from Phase 0/§7 —
   verify BookingConfirmed and related events are reliably delivered to
   at least a minimal real consumer (even a basic notification-service
   stub is enough to prove delivery works).

3. Observability stack per §14 — metrics, tracing, structured logging,
   alerting. Verify dashboards are actually populated with the specific
   metrics named in §14 (lock conflict rate, sweep lag, booking funnel
   drop-off, etc.), not just that the tooling is installed.

4. Secrets management — verify with a chaos-style test that secrets
   aren't recoverable from logs or error responses anywhere in the stack.

5. Local CDN mock replaced by real static hosting + a real CDN/object
   storage — verify both apps and catalog service work with zero code
   changes, only configuration changes to where asset URLs point (this is
   the actual test of whether the URL-based contract from §3.1 was
   designed correctly).

For each item, show me what you verified and how, not just that the
infrastructure is now in place. This is the last phase before the system
is genuinely production-deployable, not just feature-complete — treat the
verification bar accordingly.

If anything is ambiguous, stop and ask.
```
