"""HTTP client for payment service (mocked, Appendix A), wrapped in a
minimal circuit breaker (§7, §13: "payment service down/slow -> booking
stays PENDING -> circuit breaker; booking TTL bounds the impact").
"""
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

import httpx

PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8004")

T = TypeVar("T")


class PaymentServiceUnavailable(RuntimeError):
    """Circuit is open, or the call itself failed (transport error / 5xx)."""


class PaymentNotFound(RuntimeError):
    pass


@dataclass
class Payment:
    id: str
    booking_id: str
    amount: float
    status: str


class CircuitBreaker:
    """Closed / open / half-open. Opens after `failure_threshold`
    consecutive failures; while open, rejects calls immediately (no
    network attempt) for `recovery_timeout_seconds`; then allows one
    trial call (half-open) -- success closes it, failure reopens it."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout_seconds: float = 30.0):
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
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
            raise PaymentServiceUnavailable("circuit open -- payment service recently failed")
        try:
            result = fn()
        except PaymentServiceUnavailable:
            self._consecutive_failures += 1
            if state == "half-open" or self._consecutive_failures >= self._failure_threshold:
                self._opened_at = time.monotonic()
            raise
        else:
            self._consecutive_failures = 0
            self._opened_at = None
            return result


class PaymentClient:
    def __init__(self, base_url: str = PAYMENT_SERVICE_URL, breaker: Optional[CircuitBreaker] = None):
        self._base_url = base_url
        self._breaker = breaker or CircuitBreaker()

    def get_payment(self, payment_id: str) -> Payment:
        def _do() -> Payment:
            try:
                resp = httpx.get(f"{self._base_url}/payments/{payment_id}", timeout=3.0)
            except httpx.TransportError as exc:
                raise PaymentServiceUnavailable(str(exc)) from exc
            if resp.status_code == 404:
                raise PaymentNotFound(payment_id)
            if resp.status_code >= 500:
                raise PaymentServiceUnavailable(f"payment service returned {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            return Payment(id=data["id"], booking_id=data["booking_id"], amount=data["amount"], status=data["status"])

        return self._breaker.call(_do)
