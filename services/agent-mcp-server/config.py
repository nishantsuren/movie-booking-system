"""Centralized configuration for agent-mcp-server -- same per-service
leaf-module convention as every other service's config.py (CLAUDE.md
v22). Pure leaf module: only `import os`, nothing from elsewhere in
this service.
"""
import os

PORT = int(os.getenv("PORT", "8008"))

# The routing service -- platform_client.py's read-only tools call
# through here, same as agent-service's booking_client.py does.
BOOKING_PLATFORM_URL = os.getenv("BOOKING_PLATFORM_URL", "http://localhost:8000")
PLATFORM_CLIENT_TIMEOUT_SECONDS = 10.0

# agent-service itself -- the handle_booking_turn tool calls its
# internal-only /internal/handle-turn endpoint directly (not through
# routing -- that endpoint is deliberately not exposed there).
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://localhost:8007")
AGENT_SERVICE_TIMEOUT_SECONDS = 15.0
