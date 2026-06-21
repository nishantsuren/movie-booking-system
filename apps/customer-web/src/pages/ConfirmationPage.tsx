import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Booking } from "../types";

export default function ConfirmationPage() {
  const { bookingId } = useParams<{ bookingId: string }>();
  const [booking, setBooking] = useState<Booking | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!bookingId) return;
    api
      .getBooking(bookingId)
      .then(setBooking)
      .catch((err) => setError(`Could not load booking: ${err.message}`));
  }, [bookingId]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!booking) return <p>Loading…</p>;

  return (
    <div>
      <h1 data-testid="confirmation-heading">
        {booking.status === "CONFIRMED" ? "Booking confirmed!" : `Booking ${booking.status.toLowerCase()}`}
      </h1>
      <div className="booking-summary">
        <p>
          <strong>{booking.movie_title}</strong>
        </p>
        <p>Seats: {booking.seat_labels}</p>
        <p>Total paid: ₹{booking.price_paid.toFixed(2)}</p>
        <p>Booking ID: {booking.id}</p>
      </div>
    </div>
  );
}
