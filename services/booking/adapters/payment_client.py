"""HTTP client for payment service (mocked, Appendix A), wrapped in a
minimal circuit breaker (§7, §13: "payment service down/slow -> booking
stays PENDING -> circuit breaker; booking TTL bounds the impact").
"""
from dataclasses import dataclass
from typing import Optional

import httpx

from adapters.circuit_breaker import CircuitBreaker as _CircuitBreaker
from config import CIRCUIT_BREAKER_FAILURE_THRESHOLD, CIRCUIT_BREAKER_RECOVERY_SECONDS, PAYMENT_SERVICE_URL


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


class CircuitBreaker(_CircuitBreaker):
    """Thin payment-specific subclass: defaults `trips_on` to
    PaymentServiceUnavailable rather than the generic base's bare
    `Exception`, so constructing this without an explicit `trips_on`
    (as Phase 5's own circuit-breaker test still does) keeps raising the
    same concrete type it always did, pre-Phase-9.5-refactor."""

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout_seconds: float = CIRCUIT_BREAKER_RECOVERY_SECONDS,
        trips_on=PaymentServiceUnavailable,
    ):
        super().__init__(failure_threshold, recovery_timeout_seconds, trips_on)


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
