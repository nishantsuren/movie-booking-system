# Working with Claude Code on this project

This is the handoff point: everything up to here (design doc, implementation
plan, Phase 0 scaffold) was built in this conversation. Phases 1 onward
happen in Claude Code, on your machine, where there's persistent file
access, a real Docker daemon, and git.

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

Before your first Phase 1 session, bring the stack up once yourself and
confirm the Phase 0 exit criteria in `README.md` still pass on your
machine — you want a known-good baseline before Claude Code starts
changing things.

## The general pattern, every phase

1. Start a fresh Claude Code session for each phase (`/clear` if
   continuing in the same terminal, or a new one). Fresh context per
   phase keeps Claude Code focused on the current boundary rather than
   carrying forward assumptions from whatever was just discussed.
2. Use the phase prompt (Phase 1 is fully written out below; Phases 2–13
   have shorter prompts that lean on `docs/implementation-plan.md` for
   detail, since duplicating that document into every prompt would just
   be redundant).
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
has already happened a few times during design — it's normal, not a
failure), have Claude Code propose the specific edit, review it yourself,
and commit the doc change alongside the code change so they never drift
apart.

---

## Phase 1 prompt (ready to use)

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

## Phases 2–13: condensed prompts

Each of these assumes the same opening ("implement Phase N per
docs/implementation-plan.md, read docs/design.md §[...] first") — the
prompt content below is what to add **beyond that default**, usually the
one or two things most likely to go wrong if Claude Code defaults to a
generic implementation instead of the specific one this design calls for.

**Phase 2 — seat-layout authoring + draft lock.** Emphasize: the model is
fully freeform (§4.5) — no row/column structure anywhere, not even as an
implementation detail. The draft lock (§4.6) needs heartbeat/staleness,
not a simple boolean — and the lock-ownership check must run on *every*
mutating call against a draft, not just at acquire time.

**Phase 3 — showtime creation + materialization.** Emphasize: materialize
must fail closed after exhausted retries (§4.3) — verify showtime
creation itself fails, no orphan state. The uniqueness guard belongs on
`SHOWTIME_SEAT (showtime_id, seat_template_id)`, never on the display
label (§5.3) — this was a real bug caught during design; don't let it
reappear.

**Phase 4 — seat locking, isolated.** The highest-risk phase in the whole
plan. Insist the `SeatLocker` is built and tested with no HTTP layer at
all — genuine concurrent tests (real parallel threads/processes against
overlapping seat sets), not sequential calls dressed up as a concurrency
test. Don't let this phase drift into wiring up an API; that's Phase 5.

**Phase 5 — booking saga.** Emphasize: the conditional `UPDATE` keyed on
`SHOWTIME_SEAT`'s primary key (§5.3) is the actual correctness guarantee
— test the conflict path explicitly, not just the happy path.

**Phase 6 — reconciliation sweep.** The test list is already fully
specified in §5.4 / the Phase 6 entry — use it verbatim, especially the
three-replicas-exactly-one-acquires-the-lock test.

**Phase 7 — user service.** Emphasize: `AUTH_ENABLED` stays `false`
everywhere else. Run the full existing test suite afterward as a
regression check — this phase should break nothing built in Phases 1–6.

**Phase 8 — customer web app.** First phase introducing real frontend
tooling — confirm the framework choice before starting. Service base
URLs must come from environment config (§3.2), never hardcoded.

**Phase 9 — admin web app.** The seat-layout canvas editor is the hardest
UI surface in the project — worth its own session, separate from the
simpler CRUD forms for movies/theatres/showtimes.

**Phase 10 — auth hardening.** Build the role × endpoint access-control
matrix as an actual automated test suite, not a manual checklist — this
is explicit in the implementation plan.

**Phase 11 — chaos verification.** Cross-check against §13's failure
table first — several rows are already covered by earlier phases' tests.
This phase should only add coverage for what's genuinely untested, not
duplicate it.

**Phase 12 — load testing.** Needs a load-testing tool (k6 or Locust) —
pick one and confirm before starting.

**Phase 13 — production readiness.** Largely independent items — fine to
split across multiple sessions or tackle in a different order than
listed if that suits how you're working.
