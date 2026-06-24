"""Centralized configuration for the theatre service -- env-derived
config and magic numbers shared by (or scattered across) both admin and
customer modules, all in one place. Pulled out of main.py specifically
so admin/lock.py (and anything else) can read AUTH_ENABLED without
importing main itself -- main.py is the composition root that imports
the routers, so a router importing back from main would be a circular
import.
"""
import os

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"

BOOKING_SERVICE_URL = os.getenv("BOOKING_SERVICE_URL", "http://localhost:8003")
CATALOG_SERVICE_URL = os.getenv("CATALOG_SERVICE_URL", "http://localhost:8001")

# admin/lock.py -- seat-layout draft lock staleness threshold (§4.6):
# "~2 minutes, generous against network blips".
LOCK_STALE_MINUTES = 2

# admin/showtimes.py -- §11.3 retry policy for the showtime-creation ->
# materialize-seats call: bounded attempts, exponential backoff with
# jitter. Kept short so a real outage fails the admin's request quickly
# rather than hanging the call.
MATERIALIZE_MAX_ATTEMPTS = 3
MATERIALIZE_BASE_DELAY_SECONDS = 0.2
