"""BookingOrchestrator (design §7/§8) -- the full saga (lock -> pending ->
pay -> confirm) for the first time. Mirrors the design sample's shape
(select_seats/confirm, dependency-injected seats/locker/bookings/
payments/events) with one deliberate deviation: state transitions are
conditional SQL UPDATEs in the repositories (§5.6), not an in-process
`booking.transition_to(...)` state machine -- consistent with how every
other service in this codebase treats its database as the actual state
machine, not an in-memory one.

This class takes no database connection of its own -- callers (main.py)
construct one orchestrator per request, sharing one connection across
all the repositories passed in, and own the commit()/rollback() boundary
themselves. Nothing in here ever commits or rolls back.
"""
from datetime import datetime, timedelta, timezone

from adapters.payment_client import PaymentClient
from adapters.postgres_booking_repository import PostgresBookingRepository
from adapters.postgres_seat_repository import PostgresSeatRepository
from adapters.redis_seat_locker import RedisSeatLocker
from adapters.showtime_meta_repository import ShowtimeMetaRepository
from domain.booking import (
    Booking,
    BookingHoldExpired,
    BookingNotFound,
    ConfirmConflict,
    InvalidBookingState,
    PaymentNotValid,
    SeatsUnavailable,
    ShowtimeNotMaterialized,
)

BOOKING_HOLD_SECONDS = 600  # matches RedisSeatLocker's default lock TTL (§5.1/§5.4)


class BookingConfirmedEvent:
    name = "BookingConfirmed"

    def __init__(self, booking_id: str):
        self.booking_id = booking_id


class BookingOrchestrator:
    def __init__(
        self,
        seats: PostgresSeatRepository,
        locker: RedisSeatLocker,
        bookings: PostgresBookingRepository,
        showtime_meta: ShowtimeMetaRepository,
        payments: PaymentClient,
        events,
    ):
        self._seats = seats
        self._locker = locker
        self._bookings = bookings
        self._showtime_meta = showtime_meta
        self._payments = payments
        self._events = events

    def select_seats(self, showtime_id: str, seat_ids: list[str], user_id: str, idempotency_key: str) -> Booking:
        # A genuine retry of an in-flight or already-confirmed request --
        # return it directly, touching neither Redis nor SHOWTIME_SEAT
        # (§5.6, §11.1 v12).
        existing = self._bookings.get_live_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        movie_title = self._showtime_meta.get_movie_title(showtime_id)
        if movie_title is None:
            raise ShowtimeNotMaterialized(showtime_id)

        found = self._seats.get_available_for_booking(showtime_id, seat_ids)
        missing = [sid for sid in seat_ids if sid not in found]
        if missing:
            raise SeatsUnavailable(missing)
        not_available = [sid for sid in seat_ids if found[sid]["status"] != "AVAILABLE"]
        if not_available:
            raise SeatsUnavailable(not_available)

        lock_result = self._locker.acquire(showtime_id, seat_ids, holder=user_id)
        if not lock_result.success:
            raise SeatsUnavailable(lock_result.conflicting_seat_ids)

        seat_labels = ",".join(sorted(found[sid]["label"] for sid in seat_ids))
        price_paid = sum(found[sid]["price"] for sid in seat_ids)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=BOOKING_HOLD_SECONDS)

        booking = self._bookings.create_pending(
            idempotency_key, user_id, showtime_id, movie_title, seat_labels, price_paid, expires_at
        )
        if booking is None:
            # Lost a true race: a concurrent identical request won the
            # INSERT between our idempotency check above and now. We don't
            # need the lock we just took -- the winner has its own.
            self._locker.release(showtime_id, seat_ids)
            return self._bookings.get_live_by_idempotency_key(idempotency_key)

        locked_count = self._seats.lock_seats(showtime_id, seat_ids, booking.id, expires_at)
        if locked_count != len(seat_ids):
            # Redis said yes but Postgres didn't agree on every seat --
            # shouldn't happen if both layers are consistent, but fail
            # closed rather than persist a half-correct booking.
            self._locker.release(showtime_id, seat_ids)
            raise SeatsUnavailable(seat_ids)

        return booking

    def confirm(self, booking_id: str, payment_id: str) -> Booking:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.status.value == "CONFIRMED":
            return booking  # idempotent replay -- no re-execution (§11.1)
        if booking.status.value != "PENDING":
            raise InvalidBookingState(f"cannot confirm booking in status {booking.status.value}")
        if booking.is_expired():
            raise BookingHoldExpired(booking_id)

        payment = self._payments.get_payment(payment_id)  # may raise PaymentNotFound / PaymentServiceUnavailable
        if payment.booking_id != booking_id or payment.status != "SUCCESS":
            raise PaymentNotValid(f"payment {payment_id} does not validate booking {booking_id}")

        booked_seat_ids = self._seats.mark_booked(booking.showtime_id, booking_id)
        if not booked_seat_ids:
            fresh = self._bookings.get(booking_id)
            if fresh.status.value == "CONFIRMED":
                return fresh  # lost the race to a concurrent confirm (§5.6) -- idempotent
            if fresh.is_expired():
                raise BookingHoldExpired(booking_id)
            raise ConfirmConflict(booking_id)

        confirmed = self._bookings.mark_confirmed(booking_id)
        if confirmed is None:
            # The seat update won, but the booking-status update found it
            # no longer PENDING -- only possible if something else
            # mutated it concurrently outside this flow. Re-read and trust
            # whatever's there now rather than erroring on a likely-benign race.
            confirmed = self._bookings.get(booking_id)

        self._locker.release(booking.showtime_id, booked_seat_ids)
        self._events.publish(BookingConfirmedEvent(booking_id=booking_id))
        return confirmed

    def cancel(self, booking_id: str) -> Booking:
        booking = self._bookings.get(booking_id)
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.status.value == "CANCELLED":
            return booking  # idempotent
        if booking.status.value != "PENDING":
            raise InvalidBookingState(f"cannot cancel booking in status {booking.status.value}")

        cancelled = self._bookings.mark_cancelled(booking_id)
        if cancelled is None:
            return self._bookings.get(booking_id)  # lost a race to a concurrent cancel/confirm

        released_seat_ids = self._seats.release_to_available(booking.showtime_id, booking_id)
        self._locker.release(booking.showtime_id, released_seat_ids)
        return cancelled
