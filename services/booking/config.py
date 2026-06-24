"""Centralized configuration for the booking service -- every constant
controlling locking/retry/backoff/timeout/external-URL behavior lives
here, so changing one is a one-file diff instead of an adapter-by-
adapter hunt. Plain module-level constants, no framework dependency, no
imports from anywhere else in this service -- a pure leaf module, safe
for domain/application/adapters to import from without circularity.

Several of these were previously named identically (e.g.
DEFAULT_POLL_INTERVAL_SECONDS) across reconciliation_sweep.py/
theatre_outbox_relay.py/theatre_availability_sync.py despite holding
different values per worker -- harmless while each lived in its own
module's namespace, but a real collision risk once centralized here.
Renamed with a per-worker prefix below.
"""
import os

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8004")

# §5.7/Phase 9.5: MockTheatreIntegration failure-mode knobs -- always
# "success"/false in normal operation. They exist purely so the live,
# HTTP-wired process can be restarted into a failure mode for the
# failure-path tests, the same way earlier phases stopped/restarted a
# real service process to simulate a downstream outage -- there's no
# real theatre API here to actually fail on demand otherwise.
THEATRE_MOCK_HOLD_MODE = os.getenv("THEATRE_MOCK_HOLD_MODE", "success")
THEATRE_MOCK_CONFIRM_HOLD_FAILS = os.getenv("THEATRE_MOCK_CONFIRM_HOLD_FAILS", "false").lower() == "true"

# §5.1/§5.4 -- the booking hold window (BookingOrchestrator). Matches
# RedisSeatLocker's lock TTL below by construction -- both need to agree
# on how long a seat stays held.
BOOKING_HOLD_SECONDS = 600

# RedisSeatLocker (§5.1/§5.2)
REDIS_LOCK_TTL_SECONDS = 600
# §11.3-style retry policy for transient Redis connection failures.
REDIS_RETRY_ATTEMPTS = 3
REDIS_RETRY_BACKOFF_BASE_SECONDS = 0.1

# CircuitBreaker defaults shared by PaymentClient and the theatre
# integration's own breaker instance (adapters/circuit_breaker.py).
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
CIRCUIT_BREAKER_RECOVERY_SECONDS = 30.0

# ReconciliationSweepWorker (§5.4) -- "every 15-30 seconds". Distinct
# advisory lock key from the other two workers below -- must never
# collide with another key used against this database.
RECONCILIATION_LOCK_KEY = 84236501
RECONCILIATION_POLL_INTERVAL_SECONDS = 20
RECONCILIATION_LOCK_RETRY_SECONDS = 5
RECONCILIATION_BATCH_SIZE = 500

# TheatreOutboxRelayWorker (§5.7/§13) -- distinct lock key from the sweep.
OUTBOX_RELAY_LOCK_KEY = 84236502
OUTBOX_RELAY_POLL_INTERVAL_SECONDS = 10
OUTBOX_RELAY_LOCK_RETRY_SECONDS = 5
OUTBOX_RELAY_BATCH_SIZE = 100
OUTBOX_RELAY_MAX_ATTEMPTS = 5
OUTBOX_RELAY_BACKOFF_BASE_SECONDS = 2.0  # next_attempt_at = now() + base * 2**attempts

# TheatreAvailabilitySyncWorker (§5.7's shadow inventory) -- distinct
# lock key again. §5.7: "every 60s for active showtimes, less
# frequently for future ones" -- this implementation uses one interval
# for every showtime; see the worker's own docstring.
AVAILABILITY_SYNC_LOCK_KEY = 84236503
AVAILABILITY_SYNC_POLL_INTERVAL_SECONDS = 60
AVAILABILITY_SYNC_LOCK_RETRY_SECONDS = 5
AVAILABILITY_SYNC_SHOWTIME_BATCH_SIZE = 50
