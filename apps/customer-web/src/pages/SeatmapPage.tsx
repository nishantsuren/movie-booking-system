import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { getUserId } from "../lib/userId";
import { ApiError } from "../types";
import type { Seatmap } from "../types";

const PIXELS_PER_UNIT = 40;

export default function SeatmapPage() {
  const { showtimeId } = useParams<{ showtimeId: string }>();
  const navigate = useNavigate();

  const [seatmap, setSeatmap] = useState<Seatmap | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadSeatmap = useCallback(() => {
    if (!showtimeId) return;
    api
      .getSeatmap(showtimeId)
      .then(setSeatmap)
      .catch((err) => setError(`Could not load seatmap: ${err.message}`));
  }, [showtimeId]);

  useEffect(() => {
    loadSeatmap();
  }, [loadSeatmap]);

  function toggleSeat(seatId: string, status: string) {
    if (status !== "AVAILABLE") return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(seatId)) next.delete(seatId);
      else next.add(seatId);
      return next;
    });
  }

  async function handleProceed() {
    if (!showtimeId || selected.size === 0) return;
    setSubmitting(true);
    setError(null);
    try {
      const booking = await api.createBooking(showtimeId, Array.from(selected), getUserId());
      navigate(`/bookings/${booking.id}/checkout`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const conflicting: string[] = (err.body as { detail?: { conflicting_seat_ids?: string[] } })?.detail
          ?.conflicting_seat_ids ?? [];
        setError(
          conflicting.length > 0
            ? `Someone else just took ${conflicting.length === 1 ? "one of your selected seats" : "some of your selected seats"}. Please pick again.`
            : "Those seats are no longer available. Please pick again.",
        );
        setSelected((prev) => {
          const next = new Set(prev);
          conflicting.forEach((id) => next.delete(id));
          return next;
        });
        loadSeatmap();
      } else {
        setError(`Could not create booking: ${(err as Error).message}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (!seatmap) {
    return (
      <div>
        {error && <div className="error-banner">{error}</div>}
        <p>Loading seatmap…</p>
      </div>
    );
  }

  const totalPrice = seatmap.seats
    .filter((s) => selected.has(s.id))
    .reduce((sum, s) => sum + s.price, 0);

  const maxX = Math.max(...seatmap.seats.map((s) => s.x), 0);
  const maxY = Math.max(...seatmap.seats.map((s) => s.y), 0);

  return (
    <div>
      <h1>{seatmap.movie_title}</h1>
      <p>
        {seatmap.theatre_name} — {seatmap.screen_name} — {new Date(seatmap.start_time).toLocaleString()}
      </p>
      {error && <div className="error-banner">{error}</div>}

      <div
        className="seatmap"
        style={{ width: (maxX + 1) * PIXELS_PER_UNIT + 32, height: (maxY + 1) * PIXELS_PER_UNIT + 32 }}
      >
        {seatmap.seats.map((seat) => (
          <button
            key={seat.id}
            className="seat"
            data-status={seat.status}
            data-selected={selected.has(seat.id)}
            data-testid={`seat-${seat.label}`}
            style={{ left: seat.x * PIXELS_PER_UNIT + 16, top: seat.y * PIXELS_PER_UNIT + 16 }}
            disabled={seat.status !== "AVAILABLE"}
            onClick={() => toggleSeat(seat.id, seat.status)}
            title={`${seat.label} — ₹${seat.price.toFixed(2)} — ${seat.status}`}
          >
            {seat.label}
          </button>
        ))}
      </div>

      <div className="booking-summary">
        <p>
          Selected: {selected.size} seat{selected.size === 1 ? "" : "s"} — Total: ₹{totalPrice.toFixed(2)}
        </p>
        <button
          className="primary-button"
          disabled={selected.size === 0 || submitting}
          onClick={handleProceed}
          data-testid="proceed-button"
        >
          {submitting ? "Reserving…" : "Proceed to payment"}
        </button>
      </div>
    </div>
  );
}
