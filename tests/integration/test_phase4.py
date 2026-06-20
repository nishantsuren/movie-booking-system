"""Phase 4 verification criteria (implementation-plan.md):

- Concurrency test: many genuinely parallel attempts (real threads) to
  lock overlapping seat sets for the same showtime -- assert exactly the
  correct winners and zero partial-lock states (§5.1's all-or-nothing
  Lua script).
- TTL test: acquire a lock, wait past expiry, confirm a new attempt
  against the same seats succeeds with no manual cleanup (§5.4).
- Natural key-spreading sanity check: confirm one showtime's seat-lock
  keys always land on the same Redis Cluster hash slot (design v11 --
  the hash tag is on showtime_id specifically so this is now a hard
  correctness property, not just informational), while different
  showtimes land on different slots (informational only -- two random
  UUIDs landing in the same one of 16384 slots is possible by chance).
- Redis node-failure test: kill the single local Redis container mid-test,
  confirm the client's retry-with-backoff fails fast and clearly (not a
  hang) while it's down, then confirm normal operation resumes once the
  container is back -- no special fallback path, no manual client reset
  (§11.4). This does NOT exercise real replica promotion: there is no
  multi-node Redis Cluster in local dev (`docker-compose.yml` runs one
  `redis:7` container, per CLAUDE.md's stated convention), only the
  client-retry half of §11.4's story. Confirmed with user as the
  accepted scope for this phase given that infra gap.

This test module imports services/booking/adapters/redis_seat_locker.py
directly (no HTTP, no other services involved) -- it manipulates
sys.path itself since that module isn't on the normal package path.

The node-failure test (test_node_failure_retry_then_recovers) stops and
restarts the shared local `movieticket_redis` container -- disruptive to
anything else using Redis while it runs, though nothing else in this repo
does yet. It restores the container in a `finally` block regardless of
outcome.
"""
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis
from redis.crc import key_slot

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services", "booking"))
from adapters.redis_seat_locker import RedisSeatLocker, build_redis_client, lock_key  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6380")
REDIS_CONTAINER_NAME = os.environ.get("REDIS_CONTAINER_NAME", "movieticket_redis")


@pytest.fixture
def redis_client():
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def locker(redis_client):
    return RedisSeatLocker(client=redis_client)


def new_showtime_id() -> str:
    return f"phase4-{uuid.uuid4()}"


def seat_ids(n: int) -> list[str]:
    return [f"seat-{i}" for i in range(n)]


# --- concurrency: full-overlap race ---

def test_concurrent_identical_seat_set_has_exactly_one_winner(locker, redis_client):
    showtime_id = new_showtime_id()
    seats = seat_ids(20)
    n_racers = 100

    results: list[tuple[int, "object"]] = [None] * n_racers

    def race(i: int):
        results[i] = locker.acquire(showtime_id, seats, holder=f"holder-{i}")

    with ThreadPoolExecutor(max_workers=n_racers) as pool:
        pool.map(race, range(n_racers))

    winners = [i for i, r in enumerate(results) if r.success]
    losers = [i for i, r in enumerate(results) if not r.success]

    print(f"\n[full-overlap race] racers={n_racers} winners={len(winners)} losers={len(losers)}")
    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)}: {winners}"
    assert len(losers) == n_racers - 1

    # Zero partial-lock states: every loser's reported conflicts are the
    # *entire* requested seat set -- the winner's script set all keys in
    # one atomic round trip, so no racer can ever observe a half-locked set.
    for i in losers:
        assert set(results[i].conflicting_seat_ids) == set(seats), (
            f"loser {i} saw a partial conflict set {results[i].conflicting_seat_ids} "
            "-- indicates the lock was not all-or-nothing"
        )

    winner_holder = f"holder-{winners[0]}"
    for seat in seats:
        assert redis_client.get(lock_key(showtime_id, seat)) == winner_holder

    locker.release(showtime_id, seats)


# --- concurrency: partial/sliding-window overlap race ---

def test_concurrent_overlapping_seat_sets_have_disjoint_winners_and_no_partial_locks(locker, redis_client):
    showtime_id = new_showtime_id()
    n_seats = 40
    window = 3
    seats = seat_ids(n_seats)
    n_racers = n_seats - window + 1  # sliding windows [i, i+1, i+2]

    results: list[tuple[list[str], "object"]] = [None] * n_racers
    barrier = threading.Barrier(n_racers)

    def race(i: int):
        requested = seats[i:i + window]
        barrier.wait()  # maximize actual overlap in start time across threads
        results[i] = (requested, locker.acquire(showtime_id, requested, holder=f"holder-{i}"))

    with ThreadPoolExecutor(max_workers=n_racers) as pool:
        pool.map(race, range(n_racers))

    winners = [(i, req) for i, (req, r) in enumerate(results) if r.success]
    losers = [(i, req, r) for i, (req, r) in enumerate(results) if not r.success]

    print(
        f"\n[sliding-window race] racers={n_racers} winners={len(winners)} "
        f"losers={len(losers)} seats={n_seats} window={window}"
    )
    assert len(winners) + len(losers) == n_racers

    # Invariant 1: no two winners claim an overlapping seat.
    seen: dict[str, int] = {}
    for i, requested in winners:
        for seat in requested:
            assert seat not in seen, (
                f"seat {seat} claimed by both holder-{seen.get(seat)} and holder-{i} -- "
                "double lock, atomicity violated"
            )
            seen[seat] = i

    # Invariant 2 (zero partial-lock states): a winner holds ALL of its
    # requested seats; a loser holds NONE of its requested seats.
    for i, requested in winners:
        holder = f"holder-{i}"
        for seat in requested:
            assert redis_client.get(lock_key(showtime_id, seat)) == holder, (
                f"winner holder-{i} is missing seat {seat} -- partial lock"
            )

    for i, requested, result in losers:
        holder = f"holder-{i}"
        for seat in requested:
            assert redis_client.get(lock_key(showtime_id, seat)) != holder, (
                f"loser holder-{i} ended up holding seat {seat} anyway -- partial lock"
            )
        # Every conflict reported must be a seat actually locked by someone.
        for seat in result.conflicting_seat_ids:
            assert redis_client.get(lock_key(showtime_id, seat)) is not None

    locker.release(showtime_id, seats)


# --- TTL: expiry releases without manual cleanup ---

def test_ttl_expiry_releases_lock_with_no_manual_cleanup(redis_client):
    showtime_id = new_showtime_id()
    seats = seat_ids(3)
    short_ttl_locker = RedisSeatLocker(client=redis_client, ttl_seconds=2)

    first = short_ttl_locker.acquire(showtime_id, seats, holder="holder-A")
    assert first.success, first

    blocked = short_ttl_locker.acquire(showtime_id, seats, holder="holder-B")
    assert not blocked.success, "lock should still be held before TTL expiry"
    assert set(blocked.conflicting_seat_ids) == set(seats)

    time.sleep(2.5)

    after_expiry = short_ttl_locker.acquire(showtime_id, seats, holder="holder-B")
    assert after_expiry.success, "a new attempt must succeed once the TTL has expired, with no manual cleanup"

    short_ttl_locker.release(showtime_id, seats)


# --- key-spreading: one showtime colocates, different showtimes spread (v11) ---

def test_one_showtimes_seat_keys_share_a_slot_different_showtimes_differ():
    showtime_id = new_showtime_id()
    seats = seat_ids(10)
    slots = {key_slot(lock_key(showtime_id, seat).encode()) for seat in seats}
    assert len(slots) == 1, (
        f"all of one showtime's seat-lock keys must hash to the same slot (v11's hash tag), got {slots}"
    )

    other_showtime_slots = set()
    for _ in range(8):
        other_showtime_id = new_showtime_id()
        other_showtime_slots.add(key_slot(lock_key(other_showtime_id, "seat-0").encode()))

    # Informational only (§5.2 sanity check, not a hard pass/fail) -- a
    # handful of random showtime_ids landing in >1 of Redis Cluster's
    # 16384 slots is expected but not guaranteed.
    print(f"\n[key-spreading] one showtime's slot: {slots}; 8 other showtimes' slots: {sorted(other_showtime_slots)}")


# --- Redis node failure: client retry-with-backoff, then recovery ---

def _redis_container_running() -> bool:
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", REDIS_CONTAINER_NAME],
        capture_output=True, text=True,
    )
    return out.returncode == 0 and out.stdout.strip() == "true"


def _wait_for_redis_ready(client: "redis.Redis", timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            if client.ping():
                return
        except redis.exceptions.RedisError as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"Redis did not become ready within {timeout_seconds}s: {last_error}")


def test_node_failure_retry_then_recovers():
    if not _redis_container_running():
        pytest.skip(f"{REDIS_CONTAINER_NAME} is not running locally -- nothing to kill")

    client = build_redis_client(url=REDIS_URL, retry_attempts=3, backoff_base_seconds=0.1)
    locker = RedisSeatLocker(client=client)
    showtime_id = new_showtime_id()
    seats = seat_ids(2)

    try:
        subprocess.run(["docker", "stop", REDIS_CONTAINER_NAME], check=True, capture_output=True)

        started_at = time.monotonic()
        with pytest.raises(redis.exceptions.RedisError):
            locker.acquire(showtime_id, seats, holder="holder-A")
        elapsed = time.monotonic() - started_at
        print(f"\n[node-failure] call failed after {elapsed:.2f}s of retry-with-backoff while Redis was down")
        # 3 retries at ExponentialBackoff(base=0.1) is 0.2+0.4+0.8=1.4s of
        # sleep between attempts -- elapsed must reflect that backoff
        # actually ran, not just one immediate failed attempt (the bug
        # this test caught originally: redis-py's *first* connection
        # attempt raises a bare ConnectionRefusedError, a different class
        # from redis.exceptions.ConnectionError, so it silently bypassed
        # retry_on_error until the builtin ConnectionError was added too).
        assert 1.0 < elapsed < 10.0, "retry-with-backoff must actually back off, and still fail fast, not hang"
    finally:
        subprocess.run(["docker", "start", REDIS_CONTAINER_NAME], check=True, capture_output=True)
        _wait_for_redis_ready(client)

    # Recovery: same locker/client instance, no special fallback code, no
    # manual reset -- it just works again once Redis is reachable.
    recovered = locker.acquire(showtime_id, seats, holder="holder-A")
    assert recovered.success, recovered
    locker.release(showtime_id, seats)
