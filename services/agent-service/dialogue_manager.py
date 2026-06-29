"""State-pattern dialogue logic, run by an Orchestrator.

Each DialogueState owns exactly one BookingContext slot: it extracts
that slot's value from this turn's entities, validates it against the
real platform data, and writes it -- nothing else. A state knows
nothing about any other state. Orchestrator decides which states a
turn needs to pass through and in what order, composes their
individual messages into one reply, and owns the few pieces of
cross-state plumbing that fall out of that sequencing (e.g. clearing a
downstream slot when an upstream one is corrected). main.py only does
HTTP plumbing and session lookup.
"""
import calendar
import re
from abc import ABC, abstractmethod
from datetime import date, datetime

from config import CUSTOMER_WEB_BASE_URL
from context import BookingContext
from platform_client import (
    PlatformUnavailableError,
    get_booking,
    list_cities,
    list_movies,
    list_showtimes_for_movie,
    list_theatres,
)
from states import State


class DialogueState(ABC):
    @abstractmethod
    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        """Returns (resolved, message, options, link_data). resolved=True
        means this state's own slot is settled for this turn -- the
        orchestrator may move on to the next state. message is this
        state's own text only, empty when nothing changed this turn
        (slot already settled, nothing new or conflicting said).
        options is the exact list of real values this state's message
        is enumerating (e.g. the real theatre names listed when asking
        "which theatre"), for a UI to render as clickable choices --
        purely additive metadata alongside message's prose, never
        consumed by this service itself: the app is expected to send
        the clicked option's exact text back as a normal free-text
        message next turn, which the existing exact-match/resolution
        flow already handles unchanged. Empty whenever nothing is
        being asked (resolved with no message, or a hard platform-
        unavailable failure). link_data is a small dict of structural,
        never-articulated values (currently only AwaitingBookingState
        ever populates it, with a hand-off URL) -- every other state
        always returns {} here. It must never appear inside message,
        since responder.articulate()'s LLM rephrasing can mangle
        unusual strings (observed: re-cased/truncated proper nouns) --
        a mangled URL is a broken link, not just odd phrasing, so it
        travels structurally instead, merged into main.py's response
        `extra` *after* articulation runs."""
        ...


class DialogueStateMachine:
    """Generic dispatcher -- the only place that maps a State enum
    value to the DialogueState object owning its behavior."""

    def __init__(self, states: dict[State, DialogueState]):
        self._states = states

    def handle(self, state: State, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        try:
            dialogue_state = self._states[state]
        except KeyError:
            raise ValueError(f"no handler for state {state}") from None
        return dialogue_state.handle(context, entities)


class GreetingState(DialogueState):
    """Owns exactly the city slot: extract a city name from this
    turn's entities, validate it against the platform's real city
    list, and write it to context.city_id."""

    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        try:
            cities = list_cities()
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        raw_city = entities.get("city")
        matched = self._match_city(raw_city, cities)
        city_names = [city["name"] for city in cities]

        if matched is None:
            names = ", ".join(city_names)
            if raw_city:
                return False, f"We don't support {raw_city}. We support: {names}.", city_names, {}
            if context.city_id is not None:
                return True, "", [], {}
            return False, f"Which city are you in? We support: {names}.", city_names, {}

        if matched["id"] == context.city_id:
            return True, "", [], {}

        context.city_id = matched["id"]
        return True, f"Great, {matched['name']} it is!", [], {}

    def _match_city(self, raw_city: str | None, cities: list[dict]) -> dict | None:
        if not raw_city:
            return None
        needle = raw_city.strip().lower()
        for city in cities:
            if city["name"].lower() == needle:
                return city
        return None


class CollectingMovieState(DialogueState):
    """Owns exactly the movie slot: extract a movie title from this
    turn's entities, validate it against the platform's real
    currently-playing list for context.city_id, and write it to
    context.movie_id. Runs after GreetingState in priority order, so
    context.city_id already reflects this turn's answer by the time
    this runs -- this class has no idea that's true, it just reads
    whatever city_id is already there."""

    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        try:
            movies = list_movies(context.city_id)
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        if not movies:
            return False, "No movies are currently playing in your city.", [], {}

        movie_titles = [movie["title"] for movie in movies]
        titles = ", ".join(movie_titles)
        raw_movie = entities.get("movie")
        matched = self._match_movie(raw_movie, movies)

        if matched is None:
            if context.movie_id is not None and not raw_movie:
                return True, "", [], {}
            if raw_movie:
                return False, f"We couldn't find that movie here. Currently playing: {titles}.", movie_titles, {}
            return False, f"Which movie would you like to watch? Currently playing: {titles}.", movie_titles, {}

        if matched["id"] == context.movie_id:
            return True, "", [], {}

        context.movie_id = matched["id"]
        return True, f"{matched['title']} it is!", [], {}

    def _match_movie(self, raw_movie: str | None, movies: list[dict]) -> dict | None:
        if not raw_movie:
            return None
        needle = raw_movie.strip().lower()
        for movie in movies:
            if movie["title"].lower() == needle:
                return movie
        return None


class CollectingDateState(DialogueState):
    """Owns exactly the date slot: derive the real set of calendar
    dates context.movie_id is actually showing on in context.city_id
    (list_showtimes_for_movie's start_time values, deduped to one
    label per real calendar date, chronological), and write
    context.date once one is confirmed -- by typed text
    case-insensitive-substring-matching a real label, or by a button
    click (an exact echo of a label this state itself generated).
    Never invents a generic "next N days" calendar -- only real dates
    with a real showtime are ever offered. Runs after
    CollectingMovieState, before CollectingTheatreState: city+movie
    are already resolved by the time this runs. Unlike
    CollectingShowtimeState, this state never had an auto-resolve
    shortcut to remove -- it only ever writes context.date via an
    explicit match against an enumerated real option, so the
    single-value-still-needs-a-click rule falls out for free: when
    there's only one real date, date_labels (and therefore options) is
    a one-item list, and nothing resolves until that one item is
    explicitly matched/clicked.

    `_match_date` compares on two tracks because typed text and the
    platform's own label live in different formats: a day-of-week
    phrase ("the Tuesday show please") still substring-matches the
    label directly (label starts with the full weekday name), but a
    calendar-date phrase ("2nd of July", "July 2nd") shares no
    substring with the label at all ("Thursday, Jul 2") even though it
    names the same real date -- that mismatch, not a missing date, was
    a reported bug (the agent insisting a real date "isn't available"
    because the literal text never matched). `_parse_date` is the
    normalization step: build a real `date` out of the raw text --
    defaulting to the current year when the user didn't state one,
    since "2nd of July" always means the *upcoming* July 2nd in this
    conversation, never a past one -- and compare *that* against each
    candidate's real calendar date instead of comparing strings."""

    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        try:
            result = list_showtimes_for_movie(context.movie_id, context.city_id)
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        showtimes = result["showtimes"]
        movie_title = result["movie"]["title"]

        if not showtimes:
            return False, f"No showtimes are currently available for {movie_title}.", [], {}

        date_pairs = self._date_label_pairs(showtimes)
        date_labels = [label for _, label in date_pairs]

        raw_date = entities.get("date")
        matched_label = self._match_date(raw_date, date_pairs)

        if matched_label is None:
            if context.date is not None and not raw_date:
                return True, "", [], {}
            names = ", ".join(date_labels)
            if raw_date:
                return False, f"We don't have {movie_title} showing on {raw_date}. Available dates: {names}.", date_labels, {}
            return False, f"Which date would you like to watch {movie_title}? Available dates: {names}.", date_labels, {}

        if matched_label == context.date:
            return True, "", [], {}

        context.date = matched_label
        return True, f"{matched_label} it is!", [], {}

    def _date_label_pairs(self, showtimes: list[dict]) -> list[tuple[date, str]]:
        # One (real calendar date, label) pair per real calendar date,
        # deduped and sorted chronologically by the underlying ISO date
        # -- sorting the formatted label string directly would be wrong
        # across month/year boundaries ("Saturday, Jun 27" vs "Sunday,
        # Jun 28" only sorts correctly by coincidence). The date object
        # itself is kept (not just the label) so _match_date can compare
        # a parsed month/day against it directly.
        seen: dict[str, tuple[date, str]] = {}
        for st in showtimes:
            try:
                dt = datetime.fromisoformat(st["start_time"])
            except ValueError:
                continue
            seen.setdefault(dt.date().isoformat(), (dt.date(), dt.strftime("%A, %b %-d")))
        return [seen[key] for key in sorted(seen)]

    def _match_date(self, raw_date: str | None, date_pairs: list[tuple[date, str]]) -> str | None:
        if not raw_date:
            return None
        needle = raw_date.strip().lower()

        # Day-of-week / exact-label phrasing (also covers a button
        # click, which always echoes a label this state itself
        # generated): the label's own text contains the needle.
        for _, label in date_pairs:
            if needle in label.lower():
                return label

        # Calendar-date phrasing ("2nd of July", "July 2nd"): the
        # needle shares no substring with the platform's "%A, %b %-d"
        # label even though it names the same real date. Normalize the
        # needle to a real date and compare that against each
        # candidate's actual date instead of comparing text.
        parsed = self._parse_date(needle)
        if parsed is not None:
            for date_obj, label in date_pairs:
                if date_obj == parsed:
                    return label

        return None

    _MONTH_NAMES = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
    _MONTH_ABBR = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
    _ALL_MONTH_NAMES = sorted(set(_MONTH_NAMES) | set(_MONTH_ABBR), key=len, reverse=True)
    _MONTH_DAY_RE = re.compile(
        r"(\d{1,2})\s+(" + "|".join(_ALL_MONTH_NAMES) + r")"
        r"|(" + "|".join(_ALL_MONTH_NAMES) + r")\s+(\d{1,2})"
    )
    _YEAR_RE = re.compile(r"\b(\d{4})\b")

    def _parse_date(self, needle: str) -> date | None:
        # Strip ordinal suffixes ("2nd" -> "2") and the "of" in "2nd of
        # July" so both "day month" and "month day" orderings reduce to
        # a plain two-token match against _MONTH_DAY_RE.
        cleaned = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", needle)
        cleaned = cleaned.replace(" of ", " ")

        match = self._MONTH_DAY_RE.search(cleaned)
        if not match:
            return None

        if match.group(1):
            day, month_name = int(match.group(1)), match.group(2)
        else:
            month_name, day = match.group(3), int(match.group(4))

        month = self._MONTH_NAMES.get(month_name) or self._MONTH_ABBR.get(month_name)
        if month is None:
            return None

        # The user rarely states a year ("2nd of July" always means the
        # upcoming one in this conversation) -- default to the current
        # year whenever the message didn't name one explicitly.
        year_match = self._YEAR_RE.search(cleaned)
        year = int(year_match.group(1)) if year_match else date.today().year

        try:
            return date(year, month, day)
        except ValueError:
            return None


class CollectingTheatreState(DialogueState):
    """Owns exactly the theatre slot: validate a named theatre against
    the real, city-scoped theatre list, confirm it's actually
    screening context.movie_id, and write context.theatre_id. Never
    touches context.showtime_id, even when the matched theatre happens
    to have only one showtime -- "which theatre" and "which showtime"
    are two separate decisions now (CollectingShowtimeState, next in
    priority order, owns all of the latter, including the now-trivial
    single-showtime case)."""

    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        try:
            result = list_showtimes_for_movie(context.movie_id, context.city_id)
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        showtimes = result["showtimes"]
        movie_title = result["movie"]["title"]

        if not showtimes:
            return False, f"No showtimes are currently available for {movie_title}.", [], {}

        raw_theatre = entities.get("theatre")
        theatre_names = self._unique_theatre_names(showtimes)

        if not raw_theatre:
            if context.theatre_id is not None:
                return True, "", [], {}
            names = ", ".join(theatre_names)
            return False, f"Which theatre would you like? {movie_title} is showing at: {names}.", theatre_names, {}

        try:
            theatres = list_theatres(context.city_id)
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        matched_theatre = self._match_theatre(raw_theatre, theatres)
        if matched_theatre is None:
            names = ", ".join(theatre_names)
            return (
                False,
                f"We don't have a theatre called {raw_theatre} here. {movie_title} is showing at: {names}.",
                theatre_names,
                {},
            )

        at_theatre = [st for st in showtimes if st["theatre_name"] == matched_theatre["name"]]
        if not at_theatre:
            names = ", ".join(theatre_names)
            return False, f"{matched_theatre['name']} isn't showing {movie_title} right now. Try: {names}.", theatre_names, {}

        if matched_theatre["id"] == context.theatre_id:
            return True, "", [], {}

        context.theatre_id = matched_theatre["id"]
        return True, f"{matched_theatre['name']} it is!", [], {}

    def _unique_theatre_names(self, showtimes: list[dict]) -> list[str]:
        seen = []
        for showtime in showtimes:
            if showtime["theatre_name"] not in seen:
                seen.append(showtime["theatre_name"])
        return seen

    def _match_theatre(self, raw_theatre: str | None, theatres: list[dict]) -> dict | None:
        if not raw_theatre:
            return None
        needle = raw_theatre.strip().lower()
        for theatre in theatres:
            if theatre["name"].lower() == needle:
                return theatre
        return None


class CollectingShowtimeState(DialogueState):
    """Owns exactly the showtime slot: once city+movie+theatre are all
    resolved, narrow the real showtimes at context.theatre_id by
    context.date (if one was ever mentioned, persisted here across
    turns the same way every other slot persists) and resolve to a
    single one only once the user clicked one of this state's own
    option buttons (entities["showtime"], an exact echo of a label
    this state itself generated, matched directly with no ambiguity).
    Even when narrowing -- by date or otherwise -- leaves exactly one
    real candidate, it is still presented as a one-item options list
    requiring that explicit click rather than auto-written; once
    context.showtime_id already matches one of the current candidates
    (whether there's 1 or several), every later turn is a silent no-op
    regardless of candidate count -- a same-turn/later-turn re-walk
    must not re-ask forever just because more than one showtime exists
    that day. No NLU
    field or parsing exists for an arbitrary typed time-of-day phrase
    ("6pm", "the early show") -- deliberately not built (see
    agent_service_progress.md): buttons are the deterministic path
    once date narrowing alone isn't enough."""

    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        try:
            result = list_showtimes_for_movie(context.movie_id, context.city_id)
            theatres = list_theatres(context.city_id)
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        theatre = next((t for t in theatres if t["id"] == context.theatre_id), None)
        if theatre is None:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        movie_title = result["movie"]["title"]
        at_theatre = [st for st in result["showtimes"] if st["theatre_name"] == theatre["name"]]

        if not at_theatre:
            return False, f"No showtimes are currently available for {movie_title} at {theatre['name']}.", [], {}

        labelled = [(st, self._label(st["start_time"])) for st in at_theatre]

        raw_showtime = entities.get("showtime")
        if raw_showtime:
            needle = raw_showtime.strip().lower()
            for showtime, label in labelled:
                if label.lower() == needle:
                    return self._resolve(context, theatre, showtime, label)

        raw_date = entities.get("date")
        if raw_date:
            context.date = raw_date

        candidates = labelled
        if context.date:
            needle = context.date.strip().lower()
            narrowed = [(st, label) for st, label in labelled if needle in self._date_part(st["start_time"]).lower()]
            if narrowed:
                candidates = narrowed

        # Already resolved to one of the current candidates -- silent
        # no-op, the same "matched and unchanged" pattern every other
        # state already follows. Deliberately independent of candidate
        # count: this used to only check the len(candidates) == 1 case,
        # which silently broke once real seed data started having
        # multiple showtimes/day (see agent_service_progress.md) --
        # every turn after the first re-asked "which one" forever,
        # since a resolved showtime among 2+ candidates never matched
        # that narrower check. That re-ask also meant the Orchestrator's
        # walk could never reach AWAITING_BOOKING on a later turn (e.g.
        # the booking hand-off's resume turn), since this state kept
        # reporting resolved=False.
        if context.showtime_id is not None and any(st["id"] == context.showtime_id for st, _ in candidates):
            return True, "", [], {}

        labels = [label for _, label in candidates]
        names = ", ".join(labels)
        return False, f"{theatre['name']} has {movie_title} at: {names}. Which works for you?", labels, {}

    def _resolve(self, context: BookingContext, theatre: dict, showtime: dict, label: str) -> tuple[bool, str, list[str], dict]:
        if showtime["id"] == context.showtime_id:
            return True, "", [], {}
        context.showtime_id = showtime["id"]
        return True, f"{theatre['name']} on {label} it is!", [], {}

    def _date_part(self, start_time: str) -> str:
        try:
            return datetime.fromisoformat(start_time).strftime("%A, %b %-d")
        except ValueError:
            return start_time

    def _label(self, start_time: str) -> str:
        try:
            return datetime.fromisoformat(start_time).strftime("%A, %b %-d, %-I:%M %p")
        except ValueError:
            return start_time


class AwaitingBookingState(DialogueState):
    """Owns exactly context.booking_id, once city/movie/date/theatre/
    showtime are all resolved. Never writes it from typed text or a
    button -- there's no slot to extract from entities here at all;
    main.py is the only writer, from an out-of-band booking_id a
    returning browser tab supplies after completing seat selection +
    payment externally (see main.py's handle_message). This state only
    reads it, to decide what to say and which hand-off link (if any)
    belongs in link_data.

    Seat selection and payment happen entirely outside the chat, on
    customer-web's existing /showtimes/{id}/seatmap -> /bookings/{id}/
    checkout -> /bookings/{id}/confirmation pages -- this state's whole
    job is handing off to (and reporting back on) that flow, never
    re-implementing it. It is the only code in this service that knows
    those URLs' shape.

    Always resolved=True (nothing here ever blocks the priority walk)
    and options=[] (a link is not a choice to enumerate as buttons).
    Does its own get_booking() call -- one call site, colocated with
    the only code that interprets the result, same pattern every other
    state already uses for its own platform_client calls."""

    def handle(self, context: BookingContext, entities: dict) -> tuple[bool, str, list[str], dict]:
        seatmap_url = f"{CUSTOMER_WEB_BASE_URL}/showtimes/{context.showtime_id}/seatmap?agent_session_id={context.session_id}"

        if context.booking_id is None:
            return True, "Great, here's where you can pick your seats and pay -- come back here once you're done!", [], {
                "seat_selection_url": seatmap_url
            }

        try:
            booking = get_booking(context.booking_id)
        except PlatformUnavailableError:
            return False, "The booking assistant is temporarily unavailable.", [], {}

        if booking is None:
            # Unknown/stale booking_id -- treat as "nothing happened
            # yet" rather than failing the turn.
            context.booking_id = None
            return True, "We couldn't find that booking -- here's the seat selection link again:", [], {
                "seat_selection_url": seatmap_url
            }

        status = booking["status"]

        if status == "CONFIRMED":
            return True, "You're all set -- booking confirmed! Enjoy the show.", [], {}

        if status == "PENDING":
            checkout_url = f"{CUSTOMER_WEB_BASE_URL}/bookings/{context.booking_id}/checkout?agent_session_id={context.session_id}"
            return True, "Looks like payment isn't finished yet -- here's your checkout link:", [], {
                "checkout_url": checkout_url
            }

        # EXPIRED or CANCELLED -- the hold/booking is dead; clear it so
        # this state falls back into the "nothing happened yet" branch
        # next turn, and re-offer a fresh seatmap link right now too.
        context.booking_id = None
        return True, "That booking didn't go through in time -- let's try again, here's the seat selection link:", [], {
            "seat_selection_url": seatmap_url
        }


_PRIORITY_ORDER = [
    State.GREETING,
    State.COLLECTING_MOVIE,
    State.COLLECTING_DATE,
    State.COLLECTING_THEATRE,
    State.COLLECTING_SHOWTIME,
    State.AWAITING_BOOKING,
]

# Which entities field a given state's own options button-click answers
# -- the same field that state's handle() reads from entities. Lets
# main.py turn a UI button click into the right slot without needing
# to know any state's internals itself. AWAITING_BOOKING has no entry
# -- it has no button-answerable slot (its hand-off is a link, not a
# choice), so entities_from_selected_option's .get(state) correctly
# falls through to None for it.
_OPTION_FIELD_BY_STATE = {
    State.GREETING: "city",
    State.COLLECTING_MOVIE: "movie",
    State.COLLECTING_DATE: "date",
    State.COLLECTING_THEATRE: "theatre",
    State.COLLECTING_SHOWTIME: "showtime",
}


def entities_from_selected_option(state: State, selected_option: str) -> dict:
    """Builds an entities dict straight from a UI button click,
    bypassing nlu.py/resolution.py entirely. A click only ever echoes
    back text this service itself put in front of the user this same
    turn (an exact real platform name -- or, for COLLECTING_SHOWTIME, an
    exact formatted showtime label -- from that state's own options
    list) -- there's nothing left to extract or disambiguate, and
    every risk those two modules exist to guard against (mis-
    segmentation, mis-categorization) is moot for a value the user
    didn't type. state is whatever this session's last response said
    it was waiting on, i.e. exactly the slot this click is answering.
    "showtime" is never populated by nlu.py itself (no LLM field asks
    for it) -- it only ever arrives via this button-click path."""
    entities = {field: None for field in ("city", "movie", "theatre", "date", "count", "showtime")}
    field = _OPTION_FIELD_BY_STATE.get(state)
    if field is not None:
        entities[field] = selected_option
    return entities


class Orchestrator:
    """Decides which states a single turn needs to pass through and in
    what order, then composes their messages into one reply.

    Always walks the full priority list from the start, rather than
    resuming from wherever the session was last left, because each
    state's own resolved-check (matched-and-unchanged -> silent
    pass-through) already makes that cheap and correct: a city
    mentioned again is a no-op, a *different* city is a correction
    handled the exact same way a first answer is, with no separate
    correction-path needed anywhere. Stops at the first state that
    isn't resolved this turn (nothing later in the list has anything
    useful to do until that slot is filled). AWAITING_BOOKING is
    always resolved, so once every state up to it is also resolved,
    the walk simply runs to the end of the list and falls through to
    the post-loop return below, carrying whatever message/link_data
    AWAITING_BOOKING produced this turn.

    Before that walk, also resolves a named theatre to its home city
    when the user hasn't named a city directly -- GreetingState only
    ever reads entities["city"], so a theatre-derived city is injected
    there rather than CollectingTheatreState writing context.city_id
    itself, which would break the one-state-one-slot rule.
    """

    def __init__(self, machine: DialogueStateMachine):
        self._machine = machine

    def process(self, context: BookingContext, entities: dict) -> tuple[State, str, list[str], dict]:
        entities = self._resolve_city_from_theatre(entities)

        messages = []
        link_data: dict = {}
        previous_city_id = context.city_id
        previous_movie_id = context.movie_id
        previous_date = context.date
        previous_theatre_id = context.theatre_id
        previous_showtime_id = context.showtime_id

        for state in _PRIORITY_ORDER:
            resolved, message, options, link_data = self._machine.handle(state, context, entities)

            if state == State.GREETING and previous_city_id is not None and context.city_id != previous_city_id:
                # A real correction to an already-set city invalidates
                # whatever movie/date/showtime were picked against the
                # old city. Neither CollectingMovieState,
                # CollectingDateState, nor CollectingTheatreState has
                # any reason to know city_id just changed underneath it
                # -- the orchestrator, which does own the sequencing,
                # clears all three here instead. date is included here
                # (previously missed -- see the COLLECTING_MOVIE block
                # below for the same gap and why it matters) since
                # list_showtimes_for_movie's real dates are scoped by
                # city_id+movie_id, both of which are about to change.
                # booking_id is cleared here too (and in every block
                # below) rather than relying solely on the
                # COLLECTING_SHOWTIME block further down: an upstream
                # correction often makes this same turn's walk stop at
                # an earlier state (e.g. re-asking "which theatre"),
                # which would never reach that block at all -- clearing
                # booking_id immediately, at the exact point the
                # correction happens, is the only way to guarantee a
                # stale booking never survives it.
                context.movie_id = None
                context.date = None
                context.theatre_id = None
                context.showtime_id = None
                context.booking_id = None

            if state == State.COLLECTING_MOVIE and previous_movie_id is not None and context.movie_id != previous_movie_id:
                # Same idea one slot down: a date picked against the old
                # movie may not even be a real date for the new one
                # (list_showtimes_for_movie's dates are scoped by
                # movie_id) -- a stale context.date previously survived
                # this correction silently (CollectingDateState's own
                # no-op check only compares "did the user say anything
                # this turn", never "is the value I already have still
                # real for the now-current movie"), which let a booking
                # cascade through under the *old* movie's date label
                # while actually narrowing showtimes against the *new*
                # movie. A showtime/theatre picked against the old movie
                # means nothing either, even if the theatre/city didn't
                # change.
                context.date = None
                context.theatre_id = None
                context.showtime_id = None
                context.booking_id = None

            if state == State.COLLECTING_DATE and previous_date is not None and context.date != previous_date:
                # One slot down from movie, one slot up from theatre: a
                # real change to the chosen date invalidates whatever
                # theatre/showtime was picked against the old date.
                # CollectingTheatreState itself still doesn't filter by
                # date (known gap, real seed data never exercises it),
                # but CollectingShowtimeState does narrow by it, so a
                # stale showtime_id must still be cleared.
                context.theatre_id = None
                context.showtime_id = None
                context.booking_id = None

            if state == State.COLLECTING_THEATRE and previous_theatre_id is not None and context.theatre_id != previous_theatre_id:
                # One slot further down: a showtime picked at the old
                # theatre means nothing once the theatre changes, even
                # if the movie/city didn't. Note context.date is *not*
                # cleared here -- a day-of-week preference stated
                # earlier still applies to whichever theatre is picked.
                context.showtime_id = None
                context.booking_id = None

            if state == State.COLLECTING_SHOWTIME and previous_showtime_id is not None and context.showtime_id != previous_showtime_id:
                # One slot further down still: covers the one case none
                # of the blocks above do -- the *same* theatre/date
                # resolving to a *different* specific showtime (e.g. a
                # button click choosing a different time among several
                # options), with no upstream slot having changed at
                # all. A booking obtained against the old showtime
                # means nothing once the showtime changes either way.
                context.booking_id = None

            if message:
                messages.append(message)
            if not resolved:
                return state, " ".join(messages), options, link_data

        return _PRIORITY_ORDER[-1], " ".join(messages), [], link_data

    def _resolve_city_from_theatre(self, entities: dict) -> dict:
        raw_theatre = entities.get("theatre")
        if not raw_theatre or entities.get("city"):
            return entities

        try:
            theatres = list_theatres()
            cities = list_cities()
        except PlatformUnavailableError:
            return entities

        needle = raw_theatre.strip().lower()
        for theatre in theatres:
            if theatre["name"].lower() != needle:
                continue
            for city in cities:
                if city["id"] == theatre["city_id"]:
                    return {**entities, "city": city["name"]}

        return entities


_machine = DialogueStateMachine(
    {
        State.GREETING: GreetingState(),
        State.COLLECTING_MOVIE: CollectingMovieState(),
        State.COLLECTING_DATE: CollectingDateState(),
        State.COLLECTING_THEATRE: CollectingTheatreState(),
        State.COLLECTING_SHOWTIME: CollectingShowtimeState(),
        State.AWAITING_BOOKING: AwaitingBookingState(),
    }
)
_orchestrator = Orchestrator(_machine)


def handle(context: BookingContext, entities: dict) -> tuple[State, str, list[str], dict]:
    """Module-level entry point main.py calls -- delegates to the one
    Orchestrator instance, which re-derives where the conversation
    actually stands from context itself each turn rather than being
    told a "current state" to resume from."""
    return _orchestrator.process(context, entities)
