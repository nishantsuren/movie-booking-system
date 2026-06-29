# AI booking agent — requirements document

An AI agent that lets customers book movie tickets through natural
conversation, usable from a CLI or embedded in the customer web app.
Built as Option B: a state machine for all dialogue logic and API
calls, with a local LLM (Llama 3.2 3B via Ollama) used for NLU —
converting free-text user input into structured intent + entities —
and, separately, for articulation — rephrasing the state machine's
already-decided response text for tone only. No subscription, no
external AI API, runs entirely on the local machine.

The critical architectural rule throughout: every *fact* and every
*decision* in a response is produced by deterministic Python — which
city matched, what to ask next, what a seat costs. The LLM is called
up to twice per user turn: once for NLU (free text → JSON), and once
for articulation (decided text → more natural-sounding text, same
facts). Articulation may only change phrasing; it is given the exact
text already decided by templates/dialogue_manager and instructed not
to add, remove, or invent any fact, and the original text is used
verbatim if the call fails, times out, or returns something empty.
Neither LLM call ever calls a platform API or makes a decision.

---

## 1. Why Option B for booking

Booking a movie ticket is a finite information-collection task — city,
movie, date, showtime, seats — followed by a series of API calls. A
general reasoning LLM adds cost and unpredictability to a problem that
doesn't need general intelligence. What the agent needs is:

- **Intent recognition**: what does the user want to do?
- **Entity extraction**: pull structured data from free text
- **Dialogue state tracking**: remember what's been collected, know
  what to ask next
- **API execution**: call the platform's REST endpoints

The local LLM handles the first two, plus a final articulation pass
on the response text. The state machine handles the API execution and
decides every fact in the response; fixed templates are still the only
source of those facts, so the agent cannot hallucinate a showtime,
fabricate a seat, or invent booking details — articulation can only
reword the template's own text, not add to it.

---

## 2. What needs to be built

### 2.1 New: `find_seats` endpoint on booking service

```
POST /booking/showtimes/{showtime_id}/find-seats
Body: {
  "count": int,
  "preferences": {
    "adjacent": bool,           // default true
    "zone": "front"|"middle"|"back"|"any",   // default "any"
    "seat_type": "standard"|"recliner"|"any" // default "any"
  }
}
Response: [
  {
    "seats": [{"id": uuid, "label": str, "seat_type": str, "price": float}],
    "description": str,    // e.g. "Row F, seats 4-5, centre, standard"
    "total_price": float,
    "zone": str
  }
]  // up to 3 groups
```

Business logic only — no new table, no migration. Fetches available
seats using the existing `is_effectively_available` predicate (v13),
classifies into zones by `position_y`, finds adjacent groups by
`position_x`, filters and ranks by preferences and centrality. Returns
the top 3 groups as human-readable options.

### 2.2 New: `agent-service`

A new Python/FastAPI service at `:8007`. Registered in
`scripts/dev.sh` with `OLLAMA_URL`, `OLLAMA_MODEL`,
`BOOKING_PLATFORM_URL`, and `AUTH_ENABLED` env vars. Two endpoints:

```
POST /agent/message
  Body: { "session_id": str, "message": str }
  Response: {
    "session_id": str,
    "response": str,
    "state": str,
    "extra": dict   // e.g. {"payment_required": true, "booking_id": ..., "amount": ...}
  }

GET /agent/session/{session_id}     // debug — returns current state + context
GET /health                          // includes Ollama connectivity status
```

### 2.3 New: `ChatWidget` component in customer-web

A slide-in panel (380px, full height, from the right) triggered by a
"Book with AI" button added to the existing app header. Calls
`POST /agent/message` — not the Anthropic API. Renders a
`PaymentCard` inline when `extra.payment_required` is true. Generates
a UUID session ID on open, reuses it for the conversation lifetime.

---

## 3. `agent-service` internal structure

```
services/agent-service/
├── main.py              # FastAPI app, two endpoints, health
├── nlu.py               # extract() — Ollama call #1, free text → JSON
├── dialogue_manager.py  # handle(state, context, nlu) → (state, str, dict)
├── states.py            # State enum + next_missing_state()
├── context.py           # BookingContext dataclass
├── templates.py         # ALL response facts/strings decided here, render()
├── responder.py         # articulate() — Ollama call #2, rephrases
│                         # render()'s output for tone; never a source of
│                         # fact, falls back to the literal template text
│                         # on any failure
├── booking_client.py    # httpx wrapper over routing service
├── session_store.py     # in-memory dict, 30-min inactivity expiry
└── requirements.txt     # fastapi, httpx, pydantic, requests
```

### 3.1 NLU layer (`nlu.py`)

One public function: `extract(user_message: str) -> NLUResult`

Sends a single prompt to Ollama (`localhost:11434`) with:
- A tight instruction: return ONLY valid JSON, no explanation
- `format="json"` parameter (Ollama's JSON mode — forces valid output)
- 5 few-shot examples covering tricky cases (typos, mid-flow
  corrections, implicit confirmations)
- 5s timeout; on failure returns `NLUResult(intent="unclear")` —
  never raises

Output schema:
```json
{
  "intent": "book_ticket"|"cancel_booking"|"check_status"|
            "browse_movies"|"provide_info"|"confirm"|"reject"|"unclear",
  "entities": {
    "city": null,
    "movie": null,
    "date": null,
    "time_preference": null,
    "theatre": null,
    "count": null,
    "seat_preference": null,
    "showtime_choice": null,
    "seat_choice": null
  }
}
```

Few-shot examples to include in the prompt:
```
"book 2 tickets for Pushpa 2 this Saturday in Bengaluru"
→ intent=book_ticket, movie="Pushpa 2", count=2, date="Saturday", city="Bengaluru"

"nah the second one"
→ intent=confirm, showtime_choice=2

"actually make it 3 seats, recliners if possible"
→ intent=provide_info, count=3, seat_preference="recliner"

"cancel it"
→ intent=cancel_booking

"Blore evening show tomorrow"
→ intent=book_ticket, city="Blore", time_preference="evening", date="tomorrow"
```

### 3.2 `context.py`

```python
@dataclass
class BookingContext:
    session_id: str
    city_id: str | None = None
    city_name: str | None = None
    movie_id: str | None = None
    movie_title: str | None = None
    date: str | None = None
    showtimes: list[dict] = field(default_factory=list)
    showtime_id: str | None = None
    showtime_display: str | None = None
    seat_options: list[dict] = field(default_factory=list)
    seat_ids: list[str] = field(default_factory=list)
    seat_description: str | None = None
    count: int | None = None
    seat_preference: str | None = None
    booking_id: str | None = None
    expires_at: datetime | None = None
    total_price: float | None = None

    def absorb(self, entities: dict) -> None:
        """Copy non-null entities into context. Only update
        a field if currently None (don't overwrite confirmed
        values); exception: count and seat_preference are
        updatable if intent is provide_info."""

    def is_hold_expired(self) -> bool: ...
    def minutes_remaining(self) -> int | None: ...
    def reset(self) -> None: ...
```

### 3.3 States and transitions (`states.py`, `dialogue_manager.py`)

States:
```
IDLE → COLLECTING_CITY → COLLECTING_MOVIE → COLLECTING_DATE
  → SHOWING_SHOWTIMES → SHOWING_SEATS → AWAITING_PAYMENT
  → CONFIRMED → IDLE
IDLE → CHECKING_STATUS → IDLE
```

`next_missing_state(context)` — returns the first state whose
required field is None. A user providing city + movie + date + count
in one message jumps directly to `SHOWING_SHOWTIMES`.

Key transition rules:
1. **Cross-state absorption**: `context.absorb(nlu.entities)` runs on
   every turn before state-specific logic.
2. **City fuzzy matching**: check against `list_cities()` with
   lowercase substring + alias map
   `{"blore":"Bengaluru","bombay":"Mumbai","madras":"Chennai"}`.
3. **Movie matching**: case-insensitive substring against
   `search_movies(city_id)`. Multiple matches → present options and
   stay in `COLLECTING_MOVIE`. Genre/language hints ("any sci-fi",
   "something Hindi") passed as filter parameters.
4. **Date normalisation**: convert "today", "tomorrow", "Saturday",
   "this weekend" to ISO dates using Python `datetime` only.
5. **Showtime selection**: display up to 5 numbered options; store
   full list in `context.showtimes`; match by `showtime_choice` index
   or time string on the user's next turn.
6. **Seat conflict recovery**: if `select_seats` returns 409,
   immediately call `find_seats` again and present fresh options —
   never ask the user to retry manually.
7. **Hold expiry check**: in `AWAITING_PAYMENT`, check
   `context.is_hold_expired()` first on every turn. If expired, reset
   and return IDLE with the expiry message. If < 3 minutes remaining,
   prepend the warning template to the response.
8. **Cancellation from any state**: if `intent=cancel_booking` and
   `booking_id` exists, cancel immediately, reset context, return IDLE.
9. **Unknown intent**: if `intent=unclear`, repeat the current state's
   question from templates — never a generic "I don't understand".

### 3.4 `templates.py`

All response text lives here. `render(key, **kwargs)` fills
placeholders; missing placeholder raises `KeyError` immediately.

Required keys: `ASK_CITY`, `ASK_MOVIE`, `ASK_DATE`,
`SHOW_MOVIES`, `SHOW_SHOWTIMES`, `SHOW_SEAT_OPTIONS`,
`BOOKING_HELD`, `HOLD_EXPIRY_WARNING`, `HOLD_EXPIRED`,
`CONFIRMED`, `CANCELLED`, `CONFLICT_RECOVERED`,
`THEATRE_UNAVAILABLE`, `PLATFORM_ERROR`, `STATUS_CONFIRMED`,
`STATUS_PENDING`, `STATUS_EXPIRED`, `NO_MOVIES_FOUND`,
`NO_SHOWTIMES_FOUND`, `NO_SEATS_FOUND`, `CITY_NOT_FOUND`,
`ALREADY_HAS_BOOKING`, `OUT_OF_SCOPE`

### 3.5 `booking_client.py`

httpx wrapper over `BOOKING_PLATFORM_URL` (the routing service).
Raises typed exceptions — `PlatformUnavailableError`,
`SeatConflictError`, `BookingNotFoundError` — not raw status codes.
No auth forwarding for now (AUTH_ENABLED=false); add TODO for
Phase 10 when the session JWT needs to be included.

Methods: `list_cities`, `search_movies`, `get_movie`,
`get_showtimes`, `get_seatmap`, `find_seats`, `select_seats`,
`get_booking`, `confirm_booking`, `cancel_booking`,
`list_user_bookings`

### 3.6 `session_store.py`

In-memory dict keyed by session_id:
`sessions: dict[str, tuple[State, BookingContext, datetime]]`

`threading.Lock` for thread safety. Expire sessions after 30 minutes
of inactivity.

---

## 4. NLU prompt design notes

The prompt is the only tuning lever for extraction quality. When the
agent misclassifies or fails to extract an entity, the fix is almost
always adding a better few-shot example, not changing code. Keep the
full prompt in one place (`nlu.py`) and treat it as a first-class
artifact that will need iteration based on real usage.

Common failure cases to cover in few-shot examples:
- City name typos and aliases ("Blore", "Bombay", "Madras")
- Implicit confirmations ("that one", "the second", "yeah")
- Mid-flow corrections ("actually 3 seats, not 2")
- Compound first messages ("Pushpa 2, Saturday, Bengaluru, 2 tickets")
- Ambiguous dates ("this weekend", "next Friday")

---

## 5. ChatWidget (`apps/customer-web/src/components/ChatWidget/`)

Files: `ChatWidget.tsx`, `MessageList.tsx`, `InputBar.tsx`,
`PaymentCard.tsx`, `ChatWidget.css`

Behaviour:
- Opened by a "Book with AI" button added to the existing app header
  (only this addition touches existing code)
- Generates a UUID session_id on first open; reuses it for the session
- Calls `POST {VITE_API_BASE_URL}/agent/message`
- User messages right-aligned, agent messages left-aligned
- Typing indicator (three dots) while awaiting response
- When `extra.payment_required` is true: renders `PaymentCard` inline
  showing movie, showtime, seats, total, and "Pay {amount}" button.
  Clicking Pay calls `POST /payment/payments` (mocked, always
  succeeds), then immediately sends the string `"__paid__"` as the
  next message. The dialogue manager recognises this as
  `intent=confirm` directly, bypassing NLU — it never goes to Ollama
- On unreachable agent service: shows
  "The booking assistant is temporarily unavailable."

---

## 6. Conversation examples

### Happy path
```
User:  Book 2 tickets for any Hindi movie this Saturday in Bengaluru
Agent: Found 3 Hindi films in Bengaluru on Saturday:
       1. Pushpa 2 (2h 55m)
       2. Singham Returns (2h 25m)
       3. Merry Christmas (2h 12m)
       Which would you like?

User:  Pushpa 2
Agent: Showtimes on Saturday:
       1. PVR Orion Mall — 10:30 AM (from ₹300)
       2. INOX Garuda Mall — 2:15 PM (from ₹280)
       3. PVR Forum Koramangala — 6:45 PM (from ₹350)

User:  Evening
Agent: Two options for 6:45 PM at PVR Forum Koramangala:
       1. Row F, seats 5-6, centre, standard — ₹350 each = ₹700
       2. Row B, seats 8-9, recliners — ₹500 each = ₹1,000
       Which do you prefer?

User:  Option 1
Agent: Seats F5 and F6 are held for 10 minutes.
       Pushpa 2 · PVR Forum Koramangala · Sat 6:45 PM · ₹700
       [Pay ₹700]  [Cancel]

User:  [clicks Pay]
Agent: Booking confirmed! 🎬
       Booking ID: BKG-84729
       Pushpa 2 · PVR Forum Koramangala · Sat 6:45 PM
       Seats F5, F6 · ₹700 paid
```

### Hold expiry
```
Agent: Your hold has expired. Seats F5 and F6 have been released.
       Shall I search again, or find different seats for the same show?
```

### Conflict recovery
```
Agent: Those seats were just taken. Alternatives for the same show:
       1. Row F, seats 3-4, centre — ₹350 each
       2. Row G, seats 6-7, centre-right — ₹350 each
       Shall I hold one of these?
```

---

## 7. Failure scenarios

| Scenario | Agent behaviour |
|---|---|
| No movies found for city/date | Template: NO_MOVIES_FOUND — suggest another city or date |
| No showtimes for movie/city/date | Template: NO_SHOWTIMES_FOUND |
| No seat groups matching preferences | Template: NO_SEATS_FOUND — offer different count or no preference |
| select_seats → 409 | Immediately call find_seats again, present fresh options |
| select_seats → 503 | Template: THEATRE_UNAVAILABLE, stay in SHOWING_SEATS |
| Hold expires mid-conversation | Check on every turn in AWAITING_PAYMENT; reset + IDLE |
| < 3 minutes on hold | Prepend HOLD_EXPIRY_WARNING to response |
| Platform fully unreachable | Template: PLATFORM_ERROR |
| Ollama unreachable / timeout (NLU call) | Return `NLUResult(intent="unclear")`, repeat current question |
| Ollama unreachable / timeout (articulation call) | Return the literal template text unchanged — never block or drop the turn |
| Out-of-scope question | Template: OUT_OF_SCOPE — redirect to support |

---

## 8. Scope boundaries

The agent does NOT:
- Handle payment card details (agent never sees card data)
- Process refunds or cancellations of CONFIRMED bookings
- Browse external data sources or the web
- Execute admin actions (CUSTOMER role JWT only)
- Guarantee seat availability before `select_seats` succeeds
  (shadow inventory may have sync lag, §5.7 of design doc)

---

## 9. Ollama setup (prerequisite)

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Pull model (one-time, ~2GB)
ollama pull llama3.2:3b

# Verify
ollama run llama3.2:3b "reply with just the word hello"
```

Ollama runs as a background service at `localhost:11434`. No account,
no API key, no usage metering. A 3B model on a modern laptop with 8GB
RAM responds in 200–400ms per NLU call.

---

## 10. Future enhancements

- Visual seatmap inside the chat widget (SVG bird's-eye, read-only)
- Proactive hold-expiry notification (requires notification system,
  §16.3 of design doc)
- User preference memory (always prefers recliners, always INOX)
- Multi-language support (system prompt and templates localized)
- Group booking coordination
- Upgrade to Llama 3.2 11B if 3B NLU accuracy proves insufficient
  for edge cases in production usage
