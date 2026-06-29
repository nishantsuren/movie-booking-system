import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { ApiError } from "../types";
import type { Booking } from "../types";

// The countdown is purely a courtesy estimate -- the booking's actual
// expiry is enforced server-side, and (design v14) confirm no longer
// self-polices wall-clock expiry at all: it gates on state and wins any
// race it reaches the database for first. Past zero, confirm can still
// succeed for as long as the sweep worker (§5.4, every 15-30s) hasn't
// yet reclaimed the seat -- so the UI says so rather than asserting a
// hard deadline it can't actually guarantee.
function useCountdown(expiresAt: string) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  const remainingMs = new Date(expiresAt).getTime() - now;
  const remainingSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const pastNominal = remainingMs <= 0;

  return { remainingSeconds, pastNominal };
}

export default function CheckoutPage() {
  const { bookingId } = useParams<{ bookingId: string }>();
  const navigate = useNavigate();
  // Present only when this flow was reached via the agent's chat
  // hand-off link -- gates the hard-reload return redirect below, so
  // a customer checking out directly sees zero behavior change.
  const [searchParams] = useSearchParams();
  const agentSessionId = searchParams.get("agent_session_id");

  const [booking, setBooking] = useState<Booking | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);

  // The only way the chat learns a hand-off completed: a *hard*
  // reload (not navigate()) back into a fresh App.tsx mount, carrying
  // both the chat session id and the now-known booking id -- App.tsx's
  // mount-time query parse picks this up and reopens the chat. A plain
  // navigate() would never trigger that parse on a same-tab SPA
  // transition. Absent agentSessionId, behavior is exactly as before.
  function goToConfirmation(id: string) {
    if (agentSessionId) {
      window.location.href = `/bookings/${id}/confirmation?agent_session_id=${encodeURIComponent(agentSessionId)}&agent_booking_id=${id}`;
    } else {
      navigate(`/bookings/${id}/confirmation`);
    }
  }

  useEffect(() => {
    if (!bookingId) return;
    api
      .getBooking(bookingId)
      .then((result) => {
        setBooking(result);
        if (result.status === "CONFIRMED") goToConfirmation(result.id);
      })
      .catch((err) => setError(`Could not load booking: ${err.message}`));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- goToConfirmation closes over agentSessionId/navigate, both stable for this component's lifetime
  }, [bookingId]);

  const { remainingSeconds, pastNominal } = useCountdown(booking?.expires_at ?? new Date().toISOString());

  const countdownLabel = useMemo(() => {
    const minutes = Math.floor(remainingSeconds / 60);
    const seconds = remainingSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  }, [remainingSeconds]);

  async function handlePay() {
    if (!booking) return;
    setPaying(true);
    setError(null);
    try {
      const payment = await api.createPayment(booking.id, booking.price_paid);
      const confirmed = await api.confirmBooking(booking.id, payment.id);
      goToConfirmation(confirmed.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "Your hold has expired and the seat has been released. Please go back and select your seats again.",
        );
      } else if (err instanceof ApiError && err.status === 503) {
        setError("Payment service is temporarily unavailable. Your seats are still held — please try again in a moment.");
      } else {
        setError(`Payment could not be completed: ${(err as Error).message}`);
      }
    } finally {
      setPaying(false);
    }
  }

  if (!booking) {
    return (
      <div>
        {error && <div className="error-banner">{error}</div>}
        <p>Loading booking…</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Checkout</h1>
      {error && (
        <div className="error-banner" data-testid="checkout-error">
          {error}
        </div>
      )}

      <div className="booking-summary">
        <p>
          <strong>{booking.movie_title}</strong>
        </p>
        <p>Seats: {booking.seat_labels}</p>
        <p>Total: ₹{booking.price_paid.toFixed(2)}</p>
      </div>

      <p
        className={`countdown ${pastNominal ? "grace" : ""}`}
        data-testid="countdown"
        data-past-nominal={pastNominal}
      >
        {pastNominal ? "Hold window: 0:00" : `Hold expires in ${countdownLabel}`}
      </p>
      {pastNominal && (
        <p data-testid="grace-window-message">Your hold's nominal time is up.</p>
      )}

      <button className="primary-button" disabled={paying} onClick={handlePay} data-testid="pay-button">
        {paying ? "Processing…" : "Pay & confirm"}
      </button>
    </div>
  );
}
