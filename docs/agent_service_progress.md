# `services/agent-service/` progress summary

Snapshot of what's actually built, as of 2026-06-27 (third session). This is a working log for
picking the rebuild back up — `docs/ai-agent-requirements.md` is the target design; this file is
"how far have we actually gotten toward it."

## Approach

The agent was deleted (2026-06-25) and an attempt to design the *entire* contract layer up front
(full `Trigger`/transition table, NLU-vs-resolution boundary, etc.) got too complex before any
code ran. Current approach, agreed with the user: build the smallest real thing, verify it, then
grow it one turn/one state at a time. `docs/ai-agent-requirements.md` (restored by the user,
2026-06-23) describes the eventual destination — it has 9 states, a 9-field NLU schema, templates,
etc. — none of that is built yet; only read it as "where this is headed," not "what exists."

## What exists right now

Six states (`GREETING`, `COLLECTING_MOVIE`, `COLLECTING_DATE`, `COLLECTING_THEATRE`,
`COLLECTING_SHOWTIME`, `AWAITING_BOOKING`), real NLU, a deterministic post-NLU resolution layer, a
second LLM pass that only rephrases, real platform calls, dispatched through a State design pattern
+ an `Orchestrator`. A structured "options" mechanism lets a UI render buttons for any unresolved
slot, with a click bypassing NLU entirely — including, for a slot narrowed down to exactly one real
candidate (see "Single-value-still-needs-a-click fix" below). The originally-tracked NLU
disambiguation bug (see "Known-fixed" below) is resolved. Seat selection/payment/confirmation
happen entirely outside the chat now, via `AWAITING_BOOKING`'s hand-off to customer-web's existing
pages (see "Chat-to-browser booking hand-off" below).

| File | Purpose |
|---|---|
| `config.py` | `PORT`, `BOOKING_PLATFORM_URL` (routing), `CUSTOMER_WEB_BASE_URL` (customer-web's own origin, e.g. `http://localhost:5173` — used only to build `AwaitingBookingState`'s hand-off links), `OLLAMA_URL`/`OLLAMA_MODEL`/`OLLAMA_TIMEOUT_SECONDS` (NLU call), `ARTICULATION_TIMEOUT_SECONDS`/`ARTICULATION_TEMPERATURE` (articulation call), `RESOLUTION_MATCH_THRESHOLD` (resolution.py's difflib threshold, 0.6, picked empirically against the live repro scenarios below). Same per-service leaf-module convention as every other service. |
| `states.py` | `State(str, Enum)` with six members: `GREETING`, `COLLECTING_MOVIE`, `COLLECTING_DATE`, `COLLECTING_THEATRE`, `COLLECTING_SHOWTIME`, `AWAITING_BOOKING`. Pure identifiers only — no behavior; behavior lives in `dialogue_manager.py`'s `DialogueState` classes. |
| `context.py` | `BookingContext` dataclass — deliberately trimmed to only the fields actually required to book a ticket: `session_id`, `user_id`, `city_id`, `movie_id`, `date`, `theatre_id`, `showtime_id`, `count`, `seat_ids`, `booking_id`. No display fields (`city_name`, `movie_title`, etc.) — those get re-fetched from the platform when needed for a response instead of cached. `date` is consumed both by `CollectingDateState` (which writes it) and `CollectingShowtimeState` (which still narrows by it, unchanged). `booking_id` is now consumed too, by `AwaitingBookingState` — written only by `main.py`, from an out-of-band signal, never by any state itself. `user_id`/`count`/`seat_ids` remain unused — the booking hand-off design below sidesteps needing them (see "Chat-to-browser booking hand-off"). |
| `session_store.py` | In-memory `dict[str, tuple[State, BookingContext]]` + a lock, alive for the process's lifetime. `set_state()` lets a turn's resolved state be persisted back. No expiry yet. |
| `platform_client.py` | `list_cities()` — `GET /theatre/cities`. `list_movies(city_id=None)` — `GET /catalog/movies[?city=<id>]`, city optional (omitted returns every active movie across every city, for `resolution.py`'s name pool, mirroring `list_theatres`'s existing optional-city shape). `list_theatres(city_id=None)` — `GET /theatre/theatres[?city=<id>]`. `list_showtimes_for_movie(movie_id, city_id)` — `GET /theatre/movies/{movie_id}/showtimes?city=<id>`, returns `{"movie": {...}, "showtimes": [...]}`. `get_booking(booking_id) -> dict \| None` — `GET /booking/bookings/{booking_id}`, returns `None` on 404 (an unknown/stale id is "nothing to report," not a hard failure) — the one function here with that distinction, since every other lookup uses a server-chosen id that's always real. All via routing; all (except the 404 case) raise `PlatformUnavailableError` on timeout/non-2xx. |
| `nlu.py` | `extract(message) -> dict` — LLM call #1, once per turn, via local Ollama (`llama3.2:3b`), `temperature=0`. Returns raw text only: `{"city", "movie", "theatre", "date", "count", "showtime"}`. `"showtime"` is part of the canonical shape but is *never* extracted by the LLM call itself (no prompt field for it, zero prompt-risk) — it only ever arrives via a UI button click, see `dialogue_manager.entities_from_selected_option()`. Never resolves anything against the platform, never writes an id. Fails closed (all-`None`) on any Ollama/parse error. |
| `resolution.py` | `resolve(entities) -> dict` — runs immediately after `nlu.extract()`, before any `DialogueState` sees the result. Deterministic, stdlib-only (`difflib.SequenceMatcher`), no LLM call. Builds a combined pool of every real city/movie/theatre name, scores each non-empty `city`/`movie`/`theatre` candidate string against the *whole* pool (not just its own category), and lets the highest-scoring match above `RESOLUTION_MATCH_THRESHOLD` claim its real category + exact platform name — re-categorizing a misrouted fragment and clearing it from its original (wrong) slot. A short fragment fully contained in a longer real name (e.g. `"INOX"` inside `"INOX Mantri Square"`) gets a containment shortcut straight to a perfect score, since plain `ratio()` wrongly penalizes that case for sheer length difference. Fails open (returns entities unchanged) if the platform is unreachable. `date` passes through entirely untouched — this layer never resolves it. |
| `dialogue_manager.py` | A State design pattern. `DialogueState` (ABC) — one concrete subclass per state (`GreetingState`, `CollectingMovieState`, `CollectingDateState`, `CollectingTheatreState`, `CollectingShowtimeState`, `AwaitingBookingState`), each owning exactly one `BookingContext` slot, returning `(resolved: bool, message: str, options: list[str], link_data: dict)`. `link_data` is new — a small dict of structural, never-articulated values (only `AwaitingBookingState` ever populates it; every other state always returns `{}`), since a hand-off URL must never pass through `responder.articulate()`'s LLM rephrasing (documented proper-noun-mangling risk — a mangled URL is a broken link). `DialogueStateMachine` is the generic dispatcher. `Orchestrator` decides which states a turn passes through (always the full priority list `[GREETING, COLLECTING_MOVIE, COLLECTING_DATE, COLLECTING_THEATRE, COLLECTING_SHOWTIME, AWAITING_BOOKING]`, from the start, every turn), composes messages, and owns cross-state plumbing (clearing downstream slots — now including `booking_id` — on an upstream correction, deriving a city from a named theatre). `entities_from_selected_option(state, selected_option) -> dict` builds an entities dict straight from a UI button click, bypassing `nlu.py`/`resolution.py` entirely. Module-level `handle(context, entities) -> tuple[State, str, list[str], dict]` is the only thing `main.py` calls. |
| `responder.py` | `articulate(template_text) -> str` — LLM call #2, once per turn, `temperature=0.3`. Rephrases the orchestrator's already-decided text for tone only; falls back to the literal input on any failure. **Observed**: the nonzero temperature occasionally re-cases or truncates a proper noun while rephrasing (e.g. `"INOX"` → `"Inox"`, a synthetic hex-suffixed test fixture name losing its suffix) — `dialogue_manager.py`'s own state is unaffected (the fact was already written to context before this runs), but tests asserting on `response` text should do so case-insensitively / not depend on an exact name surviving rephrasing, and ideally assert on `options`/`extra` instead, which bypass articulation entirely. |
| `main.py` | FastAPI app. `GET /health`, `POST /message`. `AgentMessage` request: `session_id`, `message`, `selected_option: str \| None`, `booking_id: str \| None` (new — set only by a returning browser tab after external seat selection/payment; bypasses NLU/resolution/`entities_from_selected_option` entirely, written straight to `context.booking_id`). Response's `extra` is `{"entities": ..., **link_data}` — `link_data`'s keys (`seat_selection_url`/`checkout_url`) merged in *after* `articulate()` runs. |
| `requirements.txt` | `fastapi==0.115.0`, `uvicorn[standard]==0.32.0`, `httpx==0.27.2`. |

### Single-value-still-needs-a-click fix + new `COLLECTING_DATE` state — this session

Reported bug: a live transcript (Bengaluru → Monsoon Drift → "PVR Orion Mall") showed the agent
silently auto-resolving the one real showtime at that theatre with no button at all — "PVR Orion
Mall on Monday, June 29th at 2:00 PM... no further booking steps," with the user never asked to
confirm. Root cause: `CollectingShowtimeState.handle()` had an explicit
`if len(candidates) == 1: auto-resolve` shortcut that bypassed the click requirement whenever
exactly one real showtime remained — which is *always* true against real seed data (every theatre
has exactly one showtime per movie). Fixed by removing the shortcut: a lone candidate is now folded
into the same one-item `options`-list "ask" path as the multi-candidate case, and only treated as a
silent no-op once it's already equal to the resolved `context.showtime_id` (so a same-turn/later
re-walk doesn't re-ask forever). This is a general rule, not showtime-specific: **any state that
narrows a list of real candidates down to one must still present it as a one-item options button
requiring an explicit click, never auto-write the slot.**

While fixing this, also built the long-deferred `COLLECTING_DATE` state (`context.date` existed and
was already read by `CollectingShowtimeState` for narrowing, but nothing ever explicitly
collected/confirmed it as its own step — see "Known gaps" in the previous session's notes). Per
`docs/ai-agent-requirements.md` §3.3's target ordering, it sits between `COLLECTING_MOVIE` and
`COLLECTING_THEATRE`. `CollectingDateState` derives the real set of calendar dates
`context.movie_id` is showing on in `context.city_id` from `list_showtimes_for_movie`'s
`start_time` values (deduped to one label per real calendar date, sorted chronologically by the
underlying ISO date, never by the formatted label string) — it never invents a generic "next N
days" calendar. Unlike `CollectingShowtimeState`, this state never had an auto-resolve shortcut to
remove: it only ever writes `context.date` via an explicit match against an enumerated real option,
so the single-value-still-needs-a-click rule falls out for free. `Orchestrator.process()` gained a
4th correction-clearing block, symmetric with the existing three: a real change to `context.date`
clears `context.theatre_id`/`context.showtime_id` (a theatre/showtime picked against the old date
means nothing once the date changes). `CollectingTheatreState` itself still does **not** filter its
theatre listing by `context.date` — see "Known gaps" below.

No NLU (`nlu.py`)/`resolution.py` changes were needed — `date` was already extracted and passed
through untouched by both. No frontend changes were needed either — `MessageList.tsx` already
rendered any `options` list with `length > 0`, including a single-item list; the bug was entirely a
backend logic gap, not a rendering one.

Confirmed real seed data has only one calendar date per movie+city too (every theatre's offset for
a given movie stays within the same day — same underlying fact as "no movie+theatre combination
with more than one showtime" below), so `CollectingDateState`'s multi-date paths are tested via a
new self-contained-fixture file, `test_phase0_agent_date_selection.py`, mirroring
`test_phase0_agent_showtime_resolution.py`'s convention. Existing test files needed updates beyond
that: `test_phase0_agent_movie_selection.py`'s cascade-resolves-both-in-one-message test now stops
at `COLLECTING_DATE` instead of `COLLECTING_THEATRE` (one state earlier); both
`test_phase0_agent_theatre_selection.py` and `test_phase0_agent_showtime_resolution.py`'s
"advance the conversation" helpers needed an extra date-confirmation click inserted between the
movie and theatre turns; and `test_phase0_agent_showtime_resolution.py`'s
`test_single_showtime_resolves_automatically` was rewritten (renamed
`test_single_showtime_presented_as_option_then_resolves_on_click`) since its premise — silent
auto-resolution — is exactly what this session's fix removed. Full suite (`tests/integration/` +
`shared/tests/`) verified green after all updates; the original reported transcript was also
replayed live end-to-end against the running agent service to confirm date/theatre/showtime now
each require an explicit click before the conversation reaches "no further booking steps."

### Chat-to-browser booking hand-off + new `AWAITING_BOOKING` state — this session

The user's call: rather than building in-chat seat selection + payment (the original target
design's `SHOWING_SEATS`/`AWAITING_PAYMENT` states, and the dormant `PaymentCard`/
`AgentExtra.payment_required` path from a much earlier session), reuse customer-web's existing,
already-working pages — `/showtimes/{id}/seatmap` → `/bookings/{id}/checkout` →
`/bookings/{id}/confirmation` — and have the chat hand off to them with a link, then learn the
outcome so it can conclude. Decided mechanism, after discussion: a **return-to-chat redirect** that
only fires when the flow was actually started from the chat — a customer browsing those pages
directly must see zero behavior change.

**Mechanism**: clicking the agent's link is a real `<a href>` → full page reload (the chat doesn't
need to stay open during this). `ChatWidget` lives outside `<Routes>` in `App.tsx` and never
unmounts across *client-side* navigation, but a hard reload destroys it (fresh `session_id`, fresh
React state) — continuity has to ride in the URL query string, not in memory.
1. Agent's link: `{CUSTOMER_WEB_BASE_URL}/showtimes/{showtime_id}/seatmap?agent_session_id={session_id}`.
2. `SeatmapPage` forwards `agent_session_id` unchanged into its existing `navigate()` to Checkout.
3. `CheckoutPage`, on successful confirm: if `agent_session_id` was present, a **full reload**
   (`window.location.href`, not `navigate()`) to `/bookings/{id}/confirmation?agent_session_id=...
   &agent_booking_id={id}` — carrying the now-known booking id back for the first time. Absent →
   unchanged `navigate()`, no params. This one branch is the entire "only if initiated through
   chat" gate; a `goToConfirmation(id)` helper dedupes it across both navigate-to-confirmation call
   sites in that file.
4. Because step 3 is a hard reload, `App.tsx` remounts fresh — a one-time, mount-only parse of
   `window.location.search` (lazy `useState` initializer, no `useEffect`/`useLocation` needed)
   picks up both params together, opens the chat, and passes them to `ChatWidget` as
   `resumeSessionId`/`resumeBookingId`.
5. `ChatWidget` seeds `sessionIdRef` from `resumeSessionId` (same server-side `session_store`
   entry), skips `WELCOME_MESSAGE`, and fires one mount-only silent message carrying
   `resumeBookingId` through the new `booking_id` request field.
6. `main.py`: `booking_id` on the request is an out-of-band status update, not user input — written
   straight to `context.booking_id`, bypassing NLU/resolution/`entities_from_selected_option`.
7. `AwaitingBookingState` owns `context.booking_id`. It's the only code that knows the seatmap/
   checkout URL format, and the only call site for `platform_client.get_booking()` (own
   try/except `PlatformUnavailableError`, same pattern every other state already uses for its own
   platform calls — no special-casing needed anywhere else to thread a pre-fetched booking
   through). Branches on the booking's `status`: `None` (not yet handed off) or 404/unknown →
   offer `seat_selection_url`; `PENDING` → offer `checkout_url`; `CONFIRMED` → conclude, no link;
   `EXPIRED`/`CANCELLED` → clear `context.booking_id`, re-offer `seat_selection_url`.

**Why `link_data` exists** (see file-purpose table above): the seatmap/checkout URLs must never
pass through `responder.articulate()` — a mangled URL is a broken link, not just odd phrasing.
`DialogueState.handle()`'s return tuple grew a 4th element for exactly this; every existing state's
every `return` got a mechanical `, {}` appended (no logic change). `main.py` merges `link_data` into
`extra` *after* calling `articulate()`, so a URL structurally cannot reach the rephrasing call.

**A real bug found while building this, fixed**: the original plan was to clear `context.booking_id`
only inside a new `COLLECTING_SHOWTIME`-iteration correction block (mirroring the existing
theatre-correction-clears-showtime pattern). Tracing it through showed this misses most real cases:
correcting an *upstream* slot (date, theatre, movie, city) usually makes that same turn's walk stop
*before* ever reaching the `COLLECTING_SHOWTIME` iteration (e.g. it re-asks "which theatre" instead)
— so that block would never run, leaving a stale `booking_id` behind until some *later* turn
happened to re-resolve a showtime. Fixed by adding `context.booking_id = None` directly to **every**
existing upstream correction block (city/movie/date/theatre), not just a new showtime-specific one
— clearing it immediately at the point of correction, regardless of how far that turn's walk gets.
`test_phase0_agent_awaiting_booking.py::test_date_correction_clears_booking_id` is a regression test
for exactly this (deliberately a date correction, not a theatre one — typed free text correcting to
a synthetic, hex-suffixed theatre name is a documented `llama3.2:3b` weak spot, see
`test_phase0_agent_theatre_selection.py`'s `_LOOKS_LIKE_TEST_FIXTURE`; dates are real ISO timestamps
and extract reliably via already-verified phrasing).

**Frontend** (`apps/customer-web/src/`): `types.ts`'s `AgentExtra` gains `seat_selection_url`/
`checkout_url`. `api/client.ts`'s `sendAgentMessage` gains an optional `bookingId` param. `App.tsx`
parses `agent_session_id`+`agent_booking_id` once on mount (both must be present together).
`SeatmapPage.tsx`/`CheckoutPage.tsx` read+forward `agent_session_id` via `useSearchParams()`.
`ChatWidget.tsx` gains `resumeSessionId`/`resumeBookingId` props and the one-time resume effect.
`MessageList.tsx` renders `extra.seat_selection_url`/`extra.checkout_url` as real `<a href>`
buttons (`.chat-link-button` in `ChatWidget.css`) — never linkified from `m.text`, only ever sourced
from `extra`, consistent with the articulation-safety requirement above.

**Confirmed sidesteps two pre-existing gaps rather than needing to fix them**: `context.user_id` is
still never set — irrelevant here, since the agent never calls `createBooking` itself; `SeatmapPage`
still uses its own existing `getUserId()`. And the lack of a booking-lookup-by-user-id endpoint
(confirmed absent in `services/booking/`) never matters either, since the agent always already
holds its own `booking_id` once one exists — it's told, never needs to search for it.

New test file `tests/integration/test_phase0_agent_awaiting_booking.py` (self-contained fixtures,
same convention as `test_phase0_agent_showtime_resolution.py`): offering `seat_selection_url`;
`PENDING`/`CONFIRMED`/`CANCELLED` booking-id branches; the date-correction-clears-booking_id
regression above. `test_phase0_agent_showtime_resolution.py`'s two showtime-resolves-fully tests
needed their final-state assertion updated from `COLLECTING_SHOWTIME` to `AWAITING_BOOKING` (the
walk now cascades one state further on a fully-resolved turn). Manually verified the entire
redirect loop end-to-end in a real browser (Playwright, headless): direct browsing shows zero
behavior change (no query params, no extra reloads); the chat-originated flow shows the link,
hard-reloads to Seatmap with `agent_session_id`, SPA-navigates to Checkout preserving it,
hard-reloads back to Confirmation with both params after paying, and the chat auto-opens with the
conclusion message and no welcome text, no link buttons. Also probed a malformed/partial resume URL
(`agent_session_id` only, no `agent_booking_id`) — confirmed it falls back to completely normal
behavior, no auto-open.

Found, unrelated, while building the verification script: `ShowtimesPage.tsx`'s date-picker effect
has a pre-existing race — no stale-response guard, so rapid date edits can let an older request's
response overwrite a newer one's state. Not touched (out of scope for this feature), just routed
around in the manual verification by navigating with `?date=` directly instead of typing into the
input.

**Two follow-up bugs found and fixed by the user actually trying the feature live, right after the
above was built:**

1. **`CUSTOMER_WEB_BASE_URL`'s default pointed at a separately-run Vite dev server (`:5173`)
   instead of the already-running, already-managed `local-cdn-mock` (`:8006`)**, which serves
   customer-web's *deployed build* exactly the way it already does for admin-web (`npm run
   build:deploy` copies `dist/` into `local-cdn-mock/static/customer/`; `local-cdn-mock/main.py`
   mounts it at `/` with SPA-fallback routing — confirmed deep links like `/showtimes/{id}/seatmap`
   already 200 through it). Manual testing had started a throwaway dev server for verification and
   never reconciled the config with how this project actually runs frontends — `scripts/dev.sh`
   already starts `local-cdn-mock`, nothing extra was ever needed. Fixed: default changed to
   `http://localhost:8006`, plus `scripts/dev.sh`'s `agent` block now sets it explicitly (matching
   the existing convention every other inter-service URL there already follows). Routing does not
   proxy `local-cdn-mock` (confirmed via `routing/config.py`'s `SERVICE_MAP`) — the browser hits it
   directly, same as it already does for `/admin/`.
2. **`CollectingShowtimeState`'s silent-no-op check only ever fired for `len(candidates) == 1`.**
   Once a real movie+theatre has 2+ showtimes the same day — true for some seed data now, after
   this session's earlier seed-script change making every movie run in every Bengaluru theatre for
   10 dates × 3 shows/day — a showtime that was already resolved among several same-day candidates
   got re-asked "which one works for you?" on *every later turn*, since the no-op check's
   `len(candidates) == 1` guard never matched the multi-candidate case. This silently broke the
   booking hand-off's resume turn specifically: after paying, the redirect-back turn could never
   reach `AWAITING_BOOKING`'s conclusion, because `CollectingShowtimeState` kept reporting
   `resolved=False` and the `Orchestrator` never got past it. Fixed by replacing the
   count-conditioned check with one that's independent of candidate count: `if
   context.showtime_id is not None and any(st["id"] == context.showtime_id for st, _ in
   candidates)` → silent no-op — the same "matched and unchanged" shape every other state already
   uses, just never generalized past the single-candidate case here. Regression test:
   `test_phase0_agent_awaiting_booking.py::test_resolved_showtime_among_multiple_stays_silent_on_later_turns`
   (2 same-day showtimes, resolve one, then a second unrelated turn must still reach
   `AWAITING_BOOKING`'s `CONFIRMED` branch, not bounce back to "which one").

### Deterministic resolution layer (`resolution.py`) — previous session

Built per that session's decided next step: the NLU disambiguation bug (a theatre name
landing in `entities["city"]` or `entities["movie"]`) gets a deterministic, post-NLU fix rather
than more prompt tuning. Algorithm: for each non-empty `city`/`movie`/`theatre` candidate string,
score it against *every* real name in the platform (not just its own category) using
`difflib.SequenceMatcher.ratio()` (with a containment shortcut for short fragments, see table
above); sort all candidates by score descending; the highest scorer claims its real category and
exact name first, and any other candidate whose own best match lands on an already-claimed
category gets cleared from its original slot too (not just the literal winner) — this is what
makes the worst case, `nlu.py` splitting one phrase across multiple fields (`"INOX Mantri Square"`
→ `city="Mantri Square"`, `movie="INOX"`, `theatre="INOX Mantri Square"`, observed live), resolve
to a single correct theatre with both other slots cleared, not just the literal field that already
matched.

Verified live against the exact three repro phrases from the previous session's diagnosis:
- `"How about Orion mall"` → normalizes to `"PVR Orion Mall"`.
- `"INOX Mantri Square"` (the worst case above) → resolves to theatre, city/movie cleared.
- `"switch to PVR orion mall"` (a working substitute for `"I meant PVR orion mall"`, see "New NLU
  bugs found" below) → resolves the theatre correction.

### Structured options (clickable buttons) — this session

The user's own testing surfaced real bare-proper-noun NLU extraction failures while writing
automated tests (`"Monsoon Drift"` alone returns nothing; `"Tuesday"` alone returns nothing) —
consistent with the already-documented "small model is unreliable at free-form extraction with no
surrounding context" lesson. Decided fix, at the user's explicit direction: every unresolved
state's response also carries an `options: list[str]` — the exact real values (city names, movie
titles, theatre names, or formatted showtime labels) it's already enumerating in its prose — for a
UI to render as clickable buttons. Purely additive: the free-text/NLU/resolution path is completely
unchanged. A click sends `selected_option` instead of relying on typed text; `main.py` detects this
and calls `dialogue_manager.entities_from_selected_option(state_before, selected_option)`, which
writes the literal clicked text straight into the slot the *previous* turn's options belonged to —
no LLM call, no ambiguity, since a click only ever echoes text this service itself generated this
same conversation.

Frontend (`apps/customer-web/src/components/ChatWidget/`): `MessageList.tsx` renders the latest
agent message's `options` as buttons (`.chat-option-button` in `ChatWidget.css`), disabled while a
request is in flight. Clicking one calls `ChatWidget.tsx`'s `handleSelectOption`, which displays it
as a normal user message and sends it as `selected_option`. The moment *any* answer is given —
button click or typed text — every earlier message's options are cleared (not just once the next
reply arrives), so stale buttons can't be double-clicked during the in-flight gap. `types.ts`/
`client.ts` updated for the new request/response fields. No backend route changes were needed
beyond what's listed above — the options mechanism is fully generic per-state, so the later
`COLLECTING_SHOWTIME` state needed zero frontend changes to get working buttons.

### `COLLECTING_SHOWTIME` → `COLLECTING_THEATRE` rename + new `COLLECTING_SHOWTIME` state — this session

At the user's explicit direction: the state that resolves a theatre name was renamed from
`COLLECTING_SHOWTIME` to `COLLECTING_THEATRE` ("we are only collecting the theatre at this state,
not time as well") — renamed across `states.py`, `dialogue_manager.py` (`CollectingTheatreState`),
`nlu.py`/`resolution.py` comments, and the test file (`test_phase0_agent_theatre_selection.py`,
renamed from `..._showtime_selection.py`). `CollectingTheatreState` was also simplified to *only*
ever write `context.theatre_id` — it used to also auto-write `context.showtime_id` when a matched
theatre had exactly one showtime, and ask "which time" itself when it had more than one; both of
those are now `CollectingShowtimeState`'s job, the new fourth state, registered after
`COLLECTING_THEATRE` in `_PRIORITY_ORDER`.

`CollectingShowtimeState.handle()`: once city+movie+theatre are all resolved, fetch the real
showtimes at `context.theatre_id`, format each as a label (`"Saturday, Jun 27, 6:00 PM"`,
`datetime.strftime("%A, %b %-d, %-I:%M %p")`), and resolve to one via, in order:
1. `entities["showtime"]` (a button click) exact-matched case-insensitively against the labels —
   always succeeds unless the options changed between turns.
2. `entities["date"]` (already extracted by `nlu.py` since turn one, never consumed until now) is
   persisted to `context.date` and used to narrow the candidate list by substring match against
   each label's date portion; if narrowing actually produces a non-empty subset, it's used,
   otherwise the unfiltered list is kept (fail open, not fail confusingly).
3. If exactly one candidate remains after the above, it resolves automatically (including the
   common real-world case: one showtime, nothing to ask).
4. Otherwise, the remaining candidates are presented as both prose and `options` — "which time
   works for you?" via buttons, the deterministic path once date alone doesn't narrow enough.

Per an explicit scoping decision before building this: **no new NLU field, no date/time parsing
engine**. `"time"` is not extracted by `nlu.py` at all (see `"showtime"`'s role in the table above)
— typed free text can only narrow by date (already-extracted, zero new prompt risk); picking a
specific time among several real options is a buttons-only deterministic path. This was a direct
trade-off discussion with the user, not a default — the alternative (a 6th NLU field plus relative-
date/AM-PM parsing) was explicitly declined as disproportionate scope for a path real seed data
can't even exercise yet (see below).

`Orchestrator.process()` gained a third correction-clearing block, symmetric with the existing
two: a real change to `context.theatre_id` now clears `context.showtime_id` (a showtime picked at
the old theatre means nothing once the theatre changes) — this gap didn't exist before because the
old combined state wrote both slots atomically together. `context.date` is deliberately *not*
cleared on any correction — a day-of-week preference stated once is assumed to still apply
regardless of which movie/theatre ends up chosen.

**Real seed data has no movie+theatre combination with more than one showtime, and no movie+city
combination with more than one calendar date** (confirmed while building these states — every
seeded theatre has exactly one showtime per movie, and every theatre's offset for a given movie
stays within the same day). The multi-showtime/date-narrowing/button-click paths above, and
`CollectingDateState`'s multi-date/typed-date/button-click/correction paths, are therefore each
tested against a self-contained fixture — `tests/integration/test_phase0_agent_showtime_resolution.py`
and `tests/integration/test_phase0_agent_date_selection.py` respectively — that creates its own
movie + active release + theatre + screen + published seat layout + showtimes via the admin API,
rather than reading pre-seeded data like every other agent test file does. The full chain, in
order: `POST /catalog/admin/movies` → `POST /catalog/admin/movies/{id}/releases` → `POST
/theatre/admin/theatres` → `POST /theatre/admin/theatres/{id}/screens` → `POST
/theatre/admin/seat-layouts/draft` → lock → publish → `POST /theatre/admin/showtimes` (×N) →
`POST /theatre/admin/showtimes/{id}/activate` (×N, showtimes default `is_active=false` and are
invisible to customers until this). The single-showtime/single-date case against real seed data
no longer auto-resolves (this session's fix) — `test_phase0_agent_theatre_selection.py`'s
real-seed-data tests now assert a one-item `options` list is presented instead.

### New NLU quirks found this session (separate from the disambiguation bug, not fixed)

Live-testing the resolution layer and writing tests surfaced several *new*, separate `llama3.2:3b`
reliability issues — none of these are what `resolution.py` exists to fix (they're not
miscategorization, they're extraction failing or fabricating outright):

- **Few-shot example echo under "I meant X".** `extract("I meant PVR orion mall")` deterministically
  (`temperature=0`) returns the *literal first few-shot example's values*
  (`city="Bengaluru", movie="Pushpa 2", theatre="PVR Forum", date="Saturday", count=2"`) —
  none derived from the actual message at all. `"switch to X"` does not trigger this and was used
  as a substitute in tests. No fix attempted — flagged to the user, who chose to log it and move on
  rather than expand scope (a grounding check against the source message, or further prompt
  tuning, were the alternatives considered and declined for tonight).
- **Bare-word/casing sensitivity.** A single word or short phrase with zero surrounding sentence
  context frequently extracts as nothing at all, even when a near-identical phrase with more
  context succeeds: `"Monsoon Drift"` alone → all-`None`, but `"I want to watch Monsoon Drift"` →
  extracts fine. `"Tuesday"` alone → all-`None`, but `"the Tuesday show please"` → extracts fine.
  `"how about orion mall"` (lowercase) → all-`None`, but `"How about Orion mall"` (capitalized) →
  extracts fine, even though the prompt's own few-shot example is itself lowercase. Consistent with
  the already-documented "~80–90% per-call" extraction reliability lesson; mitigated in tests via
  verified-working phrasing, and in production via the buttons feature above (the deterministic
  alternative to ever needing free text to work).
- **`responder.articulate()` re-casing/truncating proper nouns.** Nonzero-temperature rephrasing
  occasionally changes a name's casing (`"INOX"` → `"Inox"`) or drops part of an unusual one (a
  synthetic test fixture name losing its hex suffix). Doesn't affect `context`'s underlying
  correctness (already written before this runs) — just means tests shouldn't assert exact-cased
  text survived articulation.

## State logic (`dialogue_manager.py`)

`GreetingState.handle()` (city slot only):
1. Always fetches real cities (`list_cities()`) and tries to match the message's extracted `city`
   text against them (case-insensitive exact match on name — no alias map like "Blore"→"Bengaluru"
   yet).
2. Matched and differs from (or there is no) current `context.city_id` → writes it, returns
   `(True, "Great, {name} it is!", [])`.
3. Matched and identical to current `context.city_id` (same city restated) → `(True, "", [])`,
   silent no-op.
4. Not matched, `context.city_id` already set → `(True, "", [])`, silent no-op. Still not
   explicitly decided with the user (an unsupported-city correction while one is set).
5. Not matched, no city set yet → `(False, "Which city are you in? We support: ...", <city names>)`.

`CollectingMovieState.handle()` (movie slot only, mirrors the same shape against
`list_movies(context.city_id)` and `context.movie_id`, `options` = the real titles being listed
whenever asking or rejecting):
1. No movies at all for `context.city_id` → `(False, "No movies are currently playing...", [])`.
2. Matched and differs from (or there is no) current `context.movie_id` → writes it, returns
   `(True, "{title} it is!", [])`.
3. Matched and identical to current `context.movie_id` → `(True, "", [])`, silent no-op.
4. Not matched, a movie *was* named this turn → `(False, "We couldn't find that movie here...", <titles>)`.
5. Not matched, no movie named, nothing set yet → `(False, "Which movie would you like...", <titles>)`.
6. Not matched, no movie named, one already set → `(True, "", [])`, silent no-op.

`CollectingDateState.handle()` (date slot only, against the distinct calendar dates in
`list_showtimes_for_movie(context.movie_id, context.city_id)`, deduped + sorted chronologically by
the underlying ISO date, never by the formatted label string):
1. No showtimes at all for the movie+city → `(False, "No showtimes are currently available...", [])`.
2. Typed text or a button click matches a real date label (case-insensitive substring) and differs
   from (or there is no) current `context.date` → writes it, returns `(True, "{label} it is!", [])`.
3. Matches and identical to current `context.date` → `(True, "", [])`, silent no-op.
4. No match, something *was* said this turn → `(False, "We don't have {movie} showing on {raw}.
   Available dates: ...", <date labels>)`.
5. No match, nothing said, nothing set yet → `(False, "Which date would you like...", <date labels>)`.
6. No match, nothing said, one already set → `(True, "", [])`, silent no-op.

Never had — and so never needed to remove — an auto-resolve-on-one-candidate shortcut: it only ever
writes `context.date` via an explicit match against an enumerated real option, so even a single
real date is automatically presented as a one-item `options` list (step 5) rather than auto-picked.

`CollectingTheatreState.handle()` (theatre slot only, against
`list_showtimes_for_movie(context.movie_id, context.city_id)`; never touches `context.showtime_id`
or filters by `context.date` — see "Known gaps"):
1. No showtimes at all for the movie+city → `(False, "No showtimes are currently available...", [])`.
2. No theatre named, `context.theatre_id` already set → `(True, "", [])`, silent no-op.
3. No theatre named, nothing set yet → `(False, "Which theatre would you like? ... showing at: ...", <theatre names>)`.
4. Theatre named but doesn't match any real theatre in this city → assertive
   `(False, "We don't have a theatre called {name} here. ... showing at: ...", <theatre names>)`.
5. Theatre matches a real theatre, but it isn't screening this movie → `(False, "{theatre} isn't
   showing {movie} right now. Try: ...", <theatre names>)`.
6. Theatre matches and *is* screening the movie, differs from (or there is no) current
   `context.theatre_id` → writes it, returns `(True, "{theatre} it is!", [])`.
7. That theatre is identical to current `context.theatre_id` → `(True, "", [])`, silent no-op.

`CollectingShowtimeState.handle()` (showtime slot only, against the showtimes at
`context.theatre_id` specifically, plus `context.date`):
1. No showtimes at the resolved theatre at all → `(False, "No showtimes are currently available
   for {movie} at {theatre}.", [])` (defensive; `CollectingTheatreState` already verified
   screening, so this shouldn't normally trigger).
2. `entities["showtime"]` (a button click) exact-matches a label → resolves directly, see `_resolve`.
3. `entities["date"]`, if present, is persisted to `context.date`; candidates are narrowed by
   substring match against each label's date portion if that narrows to a non-empty subset.
   (`context.date` will normally already be set by `CollectingDateState` upstream by this point —
   this is unchanged from before that state existed, and still runs even when this turn's message
   mentions a date directly, e.g. correcting it.)
4. Exactly one candidate after the above, and it's already `context.showtime_id` → `(True, "", [])`,
   silent no-op. **Otherwise — including when it's the only candidate and hasn't been clicked yet —
   falls through to step 5**, presented as a one-item `options` list. (Previously this auto-resolved
   a lone candidate without a click; that auto-resolve shortcut was the reported bug this session's
   fix removed — see "Single-value-still-needs-a-click fix" above.)
5. More than one candidate remains (or exactly one, unclicked) → `(False, "{theatre} has {movie} at:
   {labels}. Which works for you?", <labels>)`.

`AwaitingBookingState.handle()` (booking_id slot only — never written from entities, only by
`main.py` from an out-of-band signal; this is the only state whose `link_data` is ever non-empty):
1. `context.booking_id is None` → `(True, "...pick your seats and pay...", [], {"seat_selection_url": ...})`.
2. `get_booking(context.booking_id)` raises `PlatformUnavailableError` → `(False, "temporarily
   unavailable", [], {})`.
3. Returns `None` (404/unknown id) → clears `context.booking_id`, same shape as 1.
4. `status == "CONFIRMED"` → `(True, "You're all set...", [], {})` — no link, nothing left to do.
5. `status == "PENDING"` → `(True, "...finish up here...", [], {"checkout_url": ...})`.
6. `status in ("EXPIRED", "CANCELLED")` → clears `context.booking_id`, same shape as 1, with an
   explanatory prefix ("that one expired, let's try again").

Always `resolved=True` (nothing here ever blocks the priority walk) and `options=[]` (a link isn't
a choice to enumerate as buttons). Since it's last in `_PRIORITY_ORDER` and always resolves, the
turn always falls through to the post-loop `return` carrying whatever it produced — the old
hardcoded "There are no further booking steps built yet." fallthrough message is gone; this state
always has something real to say instead.

`Orchestrator.process()` ties them together — see "Structured options," "the rename + new
`COLLECTING_SHOWTIME` state," "Single-value-still-needs-a-click fix + new `COLLECTING_DATE` state,"
"Chat-to-browser booking hand-off + new `AWAITING_BOOKING` state," and the theatre→city derivation
step (`_resolve_city_from_theatre`, unchanged this session) above for why neither state calls
another directly.

### Date-format comparison bug fix (`CollectingDateState`) — this session

Reported via manual testing: asking for a movie showing July 1–10, "how about 2nd of July" got
told the movie "isn't scheduled for July 2nd" even though July 2nd was a real, listed date. Root
cause: `CollectingDateState._match_date` only ever did a case-insensitive **substring** check of
the raw typed text against the platform's own label (`"%A, %b %-d"`, e.g. `"Thursday, Jul 2"`).
That works for a day-of-week phrase (`"Tuesday"` is literally a substring of `"Tuesday, Jul 7"`)
but not for a calendar-date phrase — `"2nd of July"`/`"July 2nd"` shares no substring with
`"Thursday, Jul 2"` even though it names the same real date, since `nlu.py` extracts the date field
as raw user text, never normalized to the platform's own format.

Fixed by adding a second, fallback match path: `_parse_date` normalizes the typed text into a real
`date` (stdlib `re` + `calendar.month_name`/`month_abbr`, no new dependency, same "deterministic,
stdlib-only" convention as `resolution.py`) by extracting a `(day, month)` pair in either word order
and defaulting to the current year when none is stated (a user saying "2nd of July" always means
the upcoming one in this conversation, never a past one). `_match_date` then compares that parsed
date against each real candidate's actual `date` object (now threaded through
`_date_label_pairs`, renamed from `_unique_date_labels` to carry both the date and its label) rather
than comparing display strings. The original substring path is unchanged and still runs first (day-
of-week phrasing, and button clicks, both keep working exactly as before) — this is purely an
additive fallback for the case the substring check can't handle.

Verified live against the exact reported repro (self-contained fixture, 10 dates July 1–10):
"how about 2nd of July" now resolves directly to `"Thursday, Jul 2"` and the turn cascades straight
to `COLLECTING_THEATRE` the same turn, instead of incorrectly rejecting a real date.

**Found while verifying, unrelated, not fixed**: `nlu.py`'s `extract()` deterministically (temp=0)
returns `date: null` for `"the Friday show please"` / `"...Saturday..."` / `"...Sunday..."`, while
the identical phrasing for Monday/Tuesday/Wednesday/Thursday extracts correctly. This is the same
"bare-word/casing extraction sensitivity" category already logged below, just a specific subset of
weekday names rather than a generic bare-word issue — surfaced now only because today's date
(2026-06-29) makes `test_phase0_agent_date_selection.py` and `test_phase0_agent_awaiting_booking.py`'s
relative-date fixtures land on a Friday. Causes `test_typed_date_text_resolves_the_matching_date`,
`test_correcting_date_after_resolving_clears_theatre_and_showtime`, and
`test_date_correction_clears_booking_id` to fail *on days where "tomorrow+N" lands on Fri/Sat/Sun* —
confirmed by direct `nlu.extract()` calls, not a regression from this session's fix (`nlu.py` itself
was not touched). Logged here rather than fixed, consistent with this project's standing decision to
not keep chasing this model's prompt sensitivity (see "New NLU quirks" below) — worth revisiting if
it keeps causing test flakiness, since unlike the other logged quirks this one is calendar-dependent
rather than always-reproducible.

## Real bugs found and fixed along the way (worth not re-discovering)

- **Routing strips the path prefix before forwarding.** `routing/main.py`'s `forward()` turns
  `POST /agent/message` into `POST {agent_base}/message` — the backend service's own route must
  exclude the prefix.
- **`llama3.2:3b` is very sensitive to prompt length/structure for the extraction task.** Two
  examples, no preamble, prompt ends on a bare `JSON:` completion cue is what held up; see
  `nlu.py`'s `_PROMPT_TEMPLATE` comment. See "New NLU quirks found this session" above for further,
  separate reliability findings beyond the original disambiguation bug this lesson was first
  written for.
- **A movie's customer-visible "currently playing" status needs an active release, not just
  `is_active=true`.** `GET /catalog/movies?city=` requires a `movie_release` row for that city with
  `release_date <= today` and `actual_end_date`/`planned_end_date` (whichever is set) `>= today` —
  discovered while building `test_phase0_agent_showtime_resolution.py`'s self-contained fixtures.
- **A showtime is invisible to customers until explicitly activated.** `POST
  /theatre/admin/showtimes` creates one with `is_active=false`; `GET
  /theatre/movies/{id}/showtimes` only ever returns active ones. `POST
  /theatre/admin/showtimes/{id}/activate` (no body) flips it — also discovered while building the
  self-contained showtime-resolution fixtures.

## Known gaps / not yet decided

- **No NLU field or parsing for an arbitrary typed time-of-day phrase** (`"6pm"`, `"the early
  show"`) — explicitly scoped out this session in favor of buttons; see "the rename + new
  `COLLECTING_SHOWTIME` state" above for the full reasoning. Revisit only if real usage shows
  buttons alone aren't enough.
- **Few-shot example echo under "I meant X"** and **bare-word/casing extraction sensitivity** — see
  "New NLU quirks found this session" above. Neither has an attempted fix; logged and explicitly
  deferred per the user's direction rather than chased (this project's repeated lesson: prompt
  tuning against this model has been a trade against some other pattern, never a free win).
- City correction to an *unsupported* city while one is already set is a silent no-op (old city
  kept) — same gap exists symmetrically for an unmatched movie name, and for an unmatched/non-
  screening theatre name, when one is already set. Still not explicitly decided with the user.
- No alias/fuzzy matching for genuinely unrelated real-world names (`docs/ai-agent-requirements.md`
  §3.3's `"Blore"`→`"Bengaluru"`, `"Bombay"`→`"Mumbai"`-style aliases) —
  `resolution.py`'s difflib layer only helps with *typos/fragments* of a real name's own
  characters, not zero-character-overlap aliases. Exact case-insensitive match (plus difflib
  similarity) only, for now.
- `entities["count"]`/`context.count`/`context.seat_ids` remain extracted/declared but unused —
  seat *selection* is now handled entirely externally by `AwaitingBookingState`'s hand-off (see
  "Chat-to-browser booking hand-off" above), which never needed them. Not a gap so much as a
  superseded plan: the original idea of an in-chat seat-count/seat-picker state is no longer the
  direction, per the user's explicit decision this session.
- The ChatWidget's `PaymentCard` component (and `AgentExtra`'s `payment_required`/`amount`/`movie`/
  `showtime`/`seats` fields) remain unconnected/dormant — still true. `extra.booking_id` and the two
  new fields (`seat_selection_url`/`checkout_url`) are populated now, but via the hand-off path, not
  `PaymentCard`. Not removed; just bypassed.
- `Orchestrator.process()` re-walks the *entire* priority list from the start every turn, re-
  fetching platform data even for already-resolved slots — now six states deep (`AwaitingBookingState`
  also does a `get_booking()` call every single turn once `context.booking_id` is set, not just
  when it changed), the same deliberate-for-now trade-off as before, revisit if it becomes a real
  cost.
- `services/agent-mcp-server/` and `ChatWidget`'s actual *wiring* to the conversational flow are
  unrelated, still-open items from before this session.
- `CollectingTheatreState` does not filter its theatre listing by `context.date` — it lists every
  theatre screening the movie regardless of date, even though `context.date` is now resolved
  upstream by `CollectingDateState` before theatre is ever asked about. Not observable with real
  seed data (only one date per movie+city), so deliberately deferred rather than built speculatively
  — revisit if real usage ever has a movie+city with 2+ dates *and* a theatre that doesn't screen on
  all of them.
- **No recovery if the user abandons the booking hand-off flow** before the final reload-redirect
  fires (closes the tab, never pays, etc.) — the session just sits in `AWAITING_BOOKING` with
  `booking_id=None` indefinitely, matching `session_store.py`'s existing no-expiry posture. No
  handling either for a hold expiring *before* any booking exists (between clicking the seatmap link
  and actually selecting seats) — purely `SeatmapPage`'s own existing, unchanged concern.
- **No ownership check that a returning `booking_id` actually belongs to the session presenting
  it** — `main.py` writes whatever `body.booking_id` says into `context.booking_id` unconditionally;
  mirrors the existing trust level of `selected_option`. Same for multiple concurrent browser
  tabs/devices resuming the same `agent_session_id` — no locking beyond `session_store.py`'s
  existing single-mutex dict.
- `ShowtimesPage.tsx`'s date-picker effect has a pre-existing race (no stale-response guard) —
  found incidentally while manually verifying the hand-off, unrelated to and not fixed by this
  session's work.

## Next session

No single decided next step — open choice for whoever picks this up:
- Move to **Phase 10** (auth hardening, `AUTH_ENABLED=true` everywhere + role×endpoint matrix,
  §3.2/§15) per `CLAUDE.md`'s standing priority order — the booking flow is now end-to-end
  (collection → hand-off → conclusion), arguably a natural point to treat the agent as "far enough
  along" for now.
- Or address one of the abandoned-flow/ownership-check gaps above if real usage shows they matter.
- Or wire up `services/agent-mcp-server/` to this conversational flow (still unrelated, still open).
Don't attempt the deferred time-of-day NLU parsing or the `CollectingTheatreState` date-filtering
gap without a concrete real-usage reason (see "Known gaps").

## Last known-good verification

Full suite (`tests/integration/` + `shared/tests/`): **99 passed, 2 skipped**, this session, after:
- Removed `CollectingShowtimeState`'s single-candidate auto-resolve shortcut (the reported bug) and
  built `CollectingDateState` + `Orchestrator` correction-clearing block, with dedicated test
  coverage in `test_phase0_agent_date_selection.py` (self-contained fixtures).
- Built the chat-to-browser booking hand-off: new `AWAITING_BOOKING` state, `link_data` plumbing,
  `main.py`'s `booking_id` field, and the full frontend redirect-loop (`App.tsx`/`SeatmapPage.tsx`/
  `CheckoutPage.tsx`/`ChatWidget.tsx`/`MessageList.tsx`), with dedicated test coverage in the new
  `test_phase0_agent_awaiting_booking.py` (self-contained fixtures).
- Fixed two bugs found by the user actually trying the feature live: `CUSTOMER_WEB_BASE_URL`
  pointed at the wrong place (`:5173` dev server instead of `local-cdn-mock`'s `:8006`), and
  `CollectingShowtimeState`'s silent-no-op check didn't generalize past the single-candidate case —
  see "Chat-to-browser booking hand-off" above for both. New regression test:
  `test_phase0_agent_awaiting_booking.py::test_resolved_showtime_among_multiple_stays_silent_on_later_turns`.
- Updated `test_phase0_agent_movie_selection.py`, `test_phase0_agent_theatre_selection.py`, and
  `test_phase0_agent_showtime_resolution.py` for state-priority-order/cascade changes from both of
  the above (see their respective subsections above for exactly what changed in each).
- TypeScript (`tsc --noEmit`) and ESLint clean on all frontend changes.
- Manually replayed the exact originally-reported transcript (Bengaluru → Monsoon Drift → PVR
  Orion Mall) live against the running agent service: date, theatre, and showtime each require an
  explicit click. Separately, manually verified the full booking hand-off redirect loop end-to-end
  in a real headless browser (Playwright) — see "Chat-to-browser booking hand-off" above for what
  was checked.

**Fourth session (2026-06-29)**: full suite **96 passed, 2 skipped, 3 failed**, after the
date-format comparison fix above (`CollectingDateState._match_date`/`_parse_date`). All 3 failures
are the pre-existing `nlu.py` Friday/Saturday/Sunday date-extraction gap documented above, not a
regression — confirmed by direct `nlu.extract()` calls returning `date: null` for those weekday
names regardless of this session's changes (`nlu.py` itself untouched). Re-running on a day where
"today + a few days" doesn't land on Fri/Sat/Sun should restore the prior 99-passed baseline.
Manually replayed the exact originally-reported transcript (a 10-showtime self-contained fixture,
July 1–10, "how about 2nd of July") live against the running agent service: now resolves directly to
the real "Thursday, Jul 2" and cascades to `COLLECTING_THEATRE` the same turn, instead of wrongly
rejecting a real date.
