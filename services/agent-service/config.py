"""Centralized configuration for agent-service. Plain module-level
constants, no framework dependency, no imports from anywhere else in
this service -- a pure leaf module, safe for anything here to import
from without circularity.
"""
import os

PORT = int(os.getenv("PORT", "8007"))

# The routing service -- platform_client.py calls through here, never
# a backend service directly (every frontend/agent talks to routing
# only, per design.md §3).
BOOKING_PLATFORM_URL = os.getenv("BOOKING_PLATFORM_URL", "http://localhost:8000")
PLATFORM_CLIENT_TIMEOUT_SECONDS = 10.0

# The customer-web frontend's own origin -- used only to build the
# seatmap/checkout hand-off link AwaitingBookingState returns. This
# service has no other reason to know it; it never calls the frontend
# itself, only emits this as a string for the user to click. customer-web
# is served as a deployed static build through local-cdn-mock (`npm run
# build:deploy`, same as admin-web), not a separately-run Vite dev
# server -- local-cdn-mock owns :8006 and isn't proxied through routing
# (see scripts/dev.sh), so this points there directly.
CUSTOMER_WEB_BASE_URL = os.getenv("CUSTOMER_WEB_BASE_URL", "http://localhost:8006")

# Local Ollama -- nlu.py's NLU call. llama3.2:3b specifically (not
# qwen3:8b, also installed locally) -- the requirements doc's Option B
# picked a small model on purpose, NLU extraction doesn't need a
# bigger one.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TIMEOUT_SECONDS = 10.0

# responder.py's articulation call -- a second, separate LLM call that
# only rephrases dialogue_manager's already-decided template text, never
# the source of any fact in it. Own timeout/temperature constants since
# it's a distinct call from NLU with different tuning needs (low but
# nonzero temperature for phrasing variety; NLU stays at 0 for
# extraction determinism). Falls back to the literal template text on
# any failure, so a slow/flaky model degrades gracefully, never blocks.
ARTICULATION_TIMEOUT_SECONDS = 5.0
ARTICULATION_TEMPERATURE = 0.3

# resolution.py's deterministic difflib-similarity layer, run on every
# nlu.py extraction before any DialogueState sees it -- corrects a
# misrouted slot (e.g. a theatre name landing in entities["city"]) and
# normalizes the matched text to the platform's exact name. Picked
# empirically against the known-broken live scenarios in
# agent_service_progress.md ("INOX Mantri Square", "PVR Orion Mall");
# difflib.SequenceMatcher.ratio() on short proper nouns, not a formula.
RESOLUTION_MATCH_THRESHOLD = 0.6
