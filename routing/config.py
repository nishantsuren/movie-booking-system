"""Centralized configuration for the routing service. Plain module-level
constants, no framework dependency, no imports from anywhere else in
this service -- a pure leaf module, safe for anything here to import
from without circularity.
"""
import os

SERVICE_MAP = {
    "catalog": os.getenv("CATALOG_SERVICE_URL", "http://catalog:8000"),
    "theatre": os.getenv("THEATRE_SERVICE_URL", "http://theatre:8000"),
    "booking": os.getenv("BOOKING_SERVICE_URL", "http://booking:8000"),
    "payment": os.getenv("PAYMENT_SERVICE_URL", "http://payment:8000"),
    "user": os.getenv("USER_SERVICE_URL", "http://user:8000"),
    "agent": os.getenv("AGENT_SERVICE_URL", "http://agent:8000"),
}
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

# Was a flat 10.0 -- too tight for the hybrid agent's outer loop when a
# larger/slower Ollama model is configured. Bumped to 20.0, then 60.0 --
# both still too tight: a single qwen3:8b call can itself take up to
# agent-service's own OLLAMA_CHAT_TIMEOUT_SECONDS (60s), and one turn
# can make up to MAX_TOOL_CALL_ROUNDS (4) of those calls, so the worst
# case for one turn is a multiple of that, not a single call's latency.
# 120.0 while still evaluating qwen3:8b; not yet validated as a
# permanent value -- llama3.2:3b never needed more than the original 10s.
CLIENT_TIMEOUT_SECONDS = float(os.getenv("ROUTING_CLIENT_TIMEOUT_SECONDS", "120.0"))
