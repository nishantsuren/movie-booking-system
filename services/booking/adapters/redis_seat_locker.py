"""RedisSeatLocker — the SeatLocker component (design §5.1/§5.2/§5.4),
Phase 4. Standalone and callable directly from tests; not wired into any
HTTP endpoint yet (that's Phase 5's BookingOrchestrator, §7/§8) and there
is deliberately no BOOKING table dependency here.

Lock keys are hash-tagged on `showtime_id` (design v11): every seat-lock
key for one showtime maps to the same Redis Cluster slot, which is what
makes the multi-key Lua script below atomic on a real cluster with no
`CROSSSLOT` risk -- the original "omit hash tags so seats spread across
nodes" version of §5.2 was incompatible with §5.1's atomicity claim.
Different showtimes still land on different slots, so overall cluster
load still distributes; only one showtime's own keys are colocated.
"""
import os
from dataclasses import dataclass, field
from typing import Optional

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

from config import REDIS_LOCK_TTL_SECONDS, REDIS_RETRY_ATTEMPTS, REDIS_RETRY_BACKOFF_BASE_SECONDS

# Atomic check-then-set across every requested key in one round trip
# (§5.1). All-or-nothing: if any key already exists, nothing is set and
# the conflicting keys come back to the caller; otherwise every key is
# SET with NX EX semantics.
_ACQUIRE_SCRIPT = """
local conflicts = {}
for i, key in ipairs(KEYS) do
    if redis.call('EXISTS', key) == 1 then
        table.insert(conflicts, key)
    end
end
if #conflicts > 0 then
    return conflicts
end
for i, key in ipairs(KEYS) do
    redis.call('SET', key, ARGV[1], 'EX', ARGV[2])
end
return {}
"""


@dataclass
class LockResult:
    success: bool
    conflicting_seat_ids: list[str] = field(default_factory=list)


def lock_key(showtime_id: str, seat_id: str) -> str:
    """`{...}` is a Redis hash tag -- only the showtime_id inside it is
    used to compute the key's cluster slot (§5.2)."""
    return f"lock:{{{showtime_id}}}:{seat_id}"


def build_redis_client(
    url: Optional[str] = None,
    retry_attempts: int = REDIS_RETRY_ATTEMPTS,
    backoff_base_seconds: float = REDIS_RETRY_BACKOFF_BASE_SECONDS,
) -> "redis.Redis":
    return redis.Redis.from_url(
        url or os.environ["REDIS_URL"],
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(ExponentialBackoff(base=backoff_base_seconds, cap=2.0), retry_attempts),
        # redis.exceptions.ConnectionError covers an established connection
        # breaking mid-command; the builtin ConnectionError (which
        # ConnectionRefusedError subclasses) covers a *fresh* connection
        # attempt being refused outright -- Connection.connect()'s own
        # retry-wrapped call raises that raw OSError subtype, not the
        # redis.exceptions one, so both are needed for retry to actually
        # cover a node that's down before any connection was ever made.
        retry_on_error=[redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, ConnectionError],
    )


class RedisSeatLocker:
    def __init__(self, client: Optional["redis.Redis"] = None, ttl_seconds: int = REDIS_LOCK_TTL_SECONDS):
        self._redis = client or build_redis_client()
        self._ttl_seconds = ttl_seconds
        self._acquire_script = self._redis.register_script(_ACQUIRE_SCRIPT)

    def acquire(self, showtime_id: str, seat_ids: list[str], holder: str) -> LockResult:
        if not seat_ids:
            return LockResult(success=True)

        key_to_seat = {lock_key(showtime_id, seat_id): seat_id for seat_id in seat_ids}
        conflicts = self._acquire_script(keys=list(key_to_seat.keys()), args=[holder, self._ttl_seconds])
        if conflicts:
            return LockResult(success=False, conflicting_seat_ids=[key_to_seat[k] for k in conflicts])
        return LockResult(success=True)

    def release(self, showtime_id: str, seat_ids: list[str]) -> None:
        if not seat_ids:
            return
        keys = [lock_key(showtime_id, seat_id) for seat_id in seat_ids]
        self._redis.delete(*keys)
