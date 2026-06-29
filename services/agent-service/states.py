"""Conversation state for the booking agent. Added one at a time as
the agent's behavior actually grows, not designed ahead of need.
"""
from enum import Enum


class State(str, Enum):
    GREETING = "GREETING"
    COLLECTING_MOVIE = "COLLECTING_MOVIE"
    COLLECTING_DATE = "COLLECTING_DATE"
    COLLECTING_THEATRE = "COLLECTING_THEATRE"
    COLLECTING_SHOWTIME = "COLLECTING_SHOWTIME"
    AWAITING_BOOKING = "AWAITING_BOOKING"
