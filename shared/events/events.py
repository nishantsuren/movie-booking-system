"""EventPublisher interface (design doc §7, §15).

A no-op/log implementation for now. Swapping in a real event bus later
(Kafka/RabbitMQ) requires zero changes to any code that depends on this
interface — that's the whole point of pulling it out as an interface this
early, before there's any real event bus to wire up.
"""
import logging
from typing import Protocol

logger = logging.getLogger("events")


class DomainEvent(Protocol):
    """Marker protocol — concrete event types (BookingConfirmed, etc.) are
    defined per-service in later phases and just need a `name` property."""

    name: str


class EventPublisher(Protocol):
    def publish(self, event: DomainEvent) -> None: ...


class LoggingEventPublisher:
    """No-op implementation: logs the event instead of delivering it
    anywhere. This is the concrete EventPublisher wired into every service
    until Phase 13 introduces a real event bus."""

    def publish(self, event: DomainEvent) -> None:
        logger.info("event published (no-op publisher): %s", getattr(event, "name", event))
