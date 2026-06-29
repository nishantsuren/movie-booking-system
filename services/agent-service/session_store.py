"""In-memory session store, alive for the process's lifetime. No
expiry logic yet -- added once there's a second state worth expiring
out of.
"""
import threading

from context import BookingContext
from states import State

_lock = threading.Lock()
_sessions: dict[str, tuple[State, BookingContext]] = {}


def get_or_create(session_id: str) -> tuple[State, BookingContext]:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = (State.GREETING, BookingContext(session_id=session_id))
        return _sessions[session_id]


def set_state(session_id: str, state: State) -> None:
    """Persist a state transition decided by dialogue_manager.handle().
    context is a mutable object already shared by reference with the
    stored tuple, so only the state half needs rewriting here."""
    with _lock:
        _, context = _sessions[session_id]
        _sessions[session_id] = (state, context)
