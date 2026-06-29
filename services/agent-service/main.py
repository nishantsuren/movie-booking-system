"""Agent service. Every turn is one of three kinds:
  - free text: runs the message through nlu.extract(), then
    resolution.resolve() deterministically re-categorizes/normalizes
    the raw extraction against real platform names; or
  - a UI button click (body.selected_option set): skips both of those
    entirely -- dialogue_manager.entities_from_selected_option() writes
    the clicked text straight into the slot the previous turn's
    options list belonged to, since a click is already an exact real
    value with nothing left to extract or disambiguate; or
  - a returning browser tab reporting a booking (body.booking_id set):
    skips NLU/resolution/entities_from_selected_option entirely too --
    this is an out-of-band status update, not user input, so it's
    written straight to context.booking_id for AwaitingBookingState to
    read.
Either way dialogue_manager.handle() then decides what to do with the
result for the current state, then responder.articulate() rephrases
that decided text for tone only -- but never the 4th return element,
link_data (a hand-off URL when AwaitingBookingState has one): a mangled
URL is a broken link, not just odd phrasing, so it's merged into
`extra` after articulation, never before. Up to two LLM calls per turn
(extract + articulate) on the free-text path, one (articulate only) on
the button/booking_id paths; neither call ever decides a fact.
"""
from fastapi import FastAPI
from pydantic import BaseModel

import session_store
from dialogue_manager import entities_from_selected_option, handle
from nlu import extract
from resolution import resolve
from responder import articulate

app = FastAPI(title="Agent service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "agent"}


class AgentMessage(BaseModel):
    session_id: str
    message: str
    selected_option: str | None = None
    booking_id: str | None = None


@app.post("/message")
def handle_message(body: AgentMessage) -> dict:
    state_before, context = session_store.get_or_create(body.session_id)

    if body.booking_id is not None:
        # Out-of-band status update -- a browser tab returning from
        # external seat selection/payment, not user-typed/clicked
        # content. Bypasses NLU/resolution/entities_from_selected_option
        # entirely; AwaitingBookingState is the only thing that reads
        # context.booking_id, never entities.
        context.booking_id = body.booking_id
        entities = {}
    elif body.selected_option is not None:
        entities = entities_from_selected_option(state_before, body.selected_option)
    else:
        entities = extract(body.message)
        entities = resolve(entities)

    state, response, options, link_data = handle(context, entities)
    session_store.set_state(body.session_id, state)
    response = articulate(response)

    return {
        "session_id": body.session_id,
        "response": response,
        "state": state.value,
        "options": options,
        "extra": {"entities": entities, **link_data},
    }
