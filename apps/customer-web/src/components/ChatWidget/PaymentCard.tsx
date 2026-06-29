import { useState } from "react";
import type { AgentExtra } from "../../types";

interface PaymentCardProps {
  extra: AgentExtra;
  isPaid: boolean;
  onPay: () => Promise<void>;
}

// ai-agent-requirements.md §5: movie title, showtime, seats, total, and
// a "Pay {amount}" button. Clicking it pays (mocked, always succeeds)
// then the parent sends the post-payment signal to the agent.
export default function PaymentCard({ extra, isPaid, onPay }: PaymentCardProps) {
  const [isPaying, setIsPaying] = useState(false);
  const amount = extra.amount ?? 0;

  async function handleClick() {
    setIsPaying(true);
    try {
      await onPay();
    } finally {
      setIsPaying(false);
    }
  }

  return (
    <div className="payment-card">
      {extra.movie && <div className="payment-card-row payment-card-movie">{extra.movie}</div>}
      {extra.showtime && <div className="payment-card-row">{extra.showtime}</div>}
      {extra.seats && <div className="payment-card-row">Seats {extra.seats}</div>}
      <div className="payment-card-total">₹{amount}</div>
      <button className="primary-button payment-card-button" onClick={handleClick} disabled={isPaying || isPaid}>
        {isPaid ? "Paid ✓" : isPaying ? "Paying…" : `Pay ₹${amount}`}
      </button>
    </div>
  );
}
