import { useEffect, useRef } from "react";
import PaymentCard from "./PaymentCard";
import type { ChatMessage } from "./ChatWidget";

interface MessageListProps {
  messages: ChatMessage[];
  isTyping: boolean;
  paidBookingIds: Set<string>;
  onPay: (bookingId: string, amount: number) => Promise<void>;
  onSelectOption: (option: string) => void;
}

export default function MessageList({ messages, isTyping, paidBookingIds, onPay, onSelectOption }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  return (
    <div className="chat-widget-messages">
      {messages.map((m) => (
        <div key={m.id} className={`chat-message chat-message-${m.role}`}>
          <div className="chat-bubble">{m.text}</div>
          {/* Sourced only from extra, never linkified from m.text --
              the rephrasing LLM call can mangle unusual strings, so a
              URL must never travel through articulated prose. */}
          {m.extra?.seat_selection_url && (
            <a className="chat-link-button" href={m.extra.seat_selection_url}>
              Pick your seats
            </a>
          )}
          {m.extra?.checkout_url && (
            <a className="chat-link-button" href={m.extra.checkout_url}>
              Go to checkout
            </a>
          )}
          {m.extra?.payment_required && m.extra.booking_id != null && (
            <PaymentCard
              extra={m.extra}
              isPaid={paidBookingIds.has(m.extra.booking_id)}
              onPay={() => onPay(m.extra!.booking_id!, m.extra!.amount ?? 0)}
            />
          )}
          {m.options && m.options.length > 0 && (
            <div className="chat-options">
              {m.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="chat-option-button"
                  disabled={isTyping}
                  onClick={() => onSelectOption(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
      {isTyping && (
        <div className="chat-message chat-message-agent">
          <div className="chat-bubble chat-typing">
            <span />
            <span />
            <span />
          </div>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
