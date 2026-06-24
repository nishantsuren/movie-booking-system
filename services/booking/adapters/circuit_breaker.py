"""Generic circuit breaker (design §7/§13), extracted from
payment_client.py once theatre integration calls (§5.7) needed the exact
same closed/open/half-open behavior -- both clients live in this same
service, so sharing the class outright is the right call here (unlike
the cross-service _derive_idempotency_key duplication convention, which
exists specifically because those are separately-deployable services).
"""
import time
from typing import Callable, Optional, Type, TypeVar

from config import CIRCUIT_BREAKER_FAILURE_THRESHOLD, CIRCUIT_BREAKER_RECOVERY_SECONDS

T = TypeVar("T")


class CircuitBreaker:
    """Closed / open / half-open. Opens after `failure_threshold`
    consecutive failures; while open, rejects calls immediately (no
    downstream attempt) for `recovery_timeout_seconds`; then allows one
    trial call (half-open) -- success closes it, failure reopens it.

    `trips_on` is the exception type (or tuple of types) that counts as
    a failure -- parametrized so each caller's own "service unavailable"
    exception type plugs in without this class needing to know about it.
    """

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout_seconds: float = CIRCUIT_BREAKER_RECOVERY_SECONDS,
        trips_on: "Type[BaseException] | tuple[Type[BaseException], ...]" = Exception,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._trips_on = trips_on
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self._state() == "open"

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self._recovery_timeout_seconds:
            return "half-open"
        return "open"

    def call(self, fn: Callable[[], T]) -> T:
        state = self._state()
        if state == "open":
            raise self._open_exception()
        try:
            result = fn()
        except self._trips_on:
            self._consecutive_failures += 1
            if state == "half-open" or self._consecutive_failures >= self._failure_threshold:
                self._opened_at = time.monotonic()
            raise
        else:
            self._consecutive_failures = 0
            self._opened_at = None
            return result

    def _open_exception(self) -> BaseException:
        """Raised when short-circuiting an open breaker, without even
        attempting the call -- must be an instance of `trips_on` itself
        so callers can catch one exception type for both "the call
        failed" and "the breaker is open and rejected it outright"."""
        exc_type = self._trips_on[0] if isinstance(self._trips_on, tuple) else self._trips_on
        return exc_type("circuit open -- recent failures exceeded threshold")
