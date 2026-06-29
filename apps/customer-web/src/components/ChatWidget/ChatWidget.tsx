import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { ApiError } from "../../types";
import type { AgentExtra } from "../../types";
import MessageList from "./MessageList";
import InputBar from "./InputBar";
import "./ChatWidget.css";

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  text: string;
  extra?: AgentExtra;
  options?: string[];
}

interface ChatWidgetProps {
  isOpen: boolean;
  onClose: () => void;
  // Set together only when App.tsx's mount-time query parse found
  // both agent_session_id and agent_booking_id -- i.e. this load is
  // the hard-reload return leg of a chat-originated booking hand-off,
  // not a fresh "Book with AI" click.
  resumeSessionId?: string;
  resumeBookingId?: string;
}

// ai-agent-requirements.md §5: sent right after the ChatWidget's own
// POST /payment/payments call -- a structured signal over the chat
// channel, never shown to the customer as a real message bubble.
const PAID_SENTINEL = "__paid__";

const WELCOME_MESSAGE: ChatMessage = {
  id: "welcome",
  role: "agent",
  text: "Hi! I can help you book movie tickets. Which city are you in?",
};

export default function ChatWidget({ isOpen, onClose, resumeSessionId, resumeBookingId }: ChatWidgetProps) {
  // Generated once when the widget is first opened, reused for the
  // conversation's lifetime (requirements doc §5) -- a ref survives
  // re-renders without re-running the generator on every one. On the
  // hand-off return leg, reuse the same session id the agent already
  // has context for, rather than starting a fresh, unrelated one.
  const sessionIdRef = useRef<string | null>(null);
  if (sessionIdRef.current === null) {
    sessionIdRef.current = resumeSessionId ?? crypto.randomUUID();
  }

  const [messages, setMessages] = useState<ChatMessage[]>(resumeSessionId ? [] : [WELCOME_MESSAGE]);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paidBookingIds, setPaidBookingIds] = useState<Set<string>>(new Set());

  async function sendMessage(text: string, visibleToUser = true, selectedOption?: string, bookingId?: string): Promise<void> {
    if (visibleToUser) {
      setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", text }]);
    }
    // Once the user has answered -- by clicking a button or by typing
    // free text -- every earlier message's option buttons stop being
    // a live choice, even before the new agent response comes back:
    // clearing options here (not just "is this the last message")
    // disables them for the whole isSending gap, not only after.
    setMessages((prev) => prev.map((m) => (m.options ? { ...m, options: undefined } : m)));
    setIsSending(true);
    setError(null);
    try {
      const result = await api.sendAgentMessage(sessionIdRef.current!, text, selectedOption, bookingId);
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "agent", text: result.response, extra: result.extra, options: result.options },
      ]);
    } catch {
      setError("The booking assistant is temporarily unavailable.");
    } finally {
      setIsSending(false);
    }
  }

  // Hand-off return leg only: report the now-known booking id back to
  // the agent exactly once, silently (never shown as a real message
  // bubble) -- the resulting agent reply is what tells the user
  // whether their booking actually went through.
  const hasSentResumeRef = useRef(false);
  useEffect(() => {
    if (resumeBookingId && !hasSentResumeRef.current) {
      hasSentResumeRef.current = true;
      void sendMessage("", false, undefined, resumeBookingId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally mount-only; resumeBookingId is fixed for this component's lifetime (App.tsx only ever sets it once, from a fresh page load)
  }, []);

  // A button click both displays as a normal user message (so the
  // transcript reads the same either way) and is sent as
  // selected_option, not as message text the backend would otherwise
  // run through NLU -- a click is already an exact, real value.
  function handleSelectOption(option: string): void {
    void sendMessage(option, true, option);
  }

  async function handlePay(bookingId: string, amount: number): Promise<void> {
    try {
      await api.createPayment(bookingId, amount);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "Payment failed -- please try again."
          : "The booking assistant is temporarily unavailable.",
      );
      return;
    }
    setPaidBookingIds((prev) => new Set(prev).add(bookingId));
    await sendMessage(PAID_SENTINEL, false);
  }

  if (!isOpen) {
    return null;
  }

  return (
    <div className="chat-widget" role="dialog" aria-label="Book with AI">
      <div className="chat-widget-header">
        <span>Book with AI</span>
        <button className="chat-widget-close" onClick={onClose} aria-label="Close chat">
          ×
        </button>
      </div>
      <MessageList
        messages={messages}
        isTyping={isSending}
        paidBookingIds={paidBookingIds}
        onPay={handlePay}
        onSelectOption={handleSelectOption}
      />
      {error && <div className="chat-widget-error">{error}</div>}
      <InputBar onSend={(text) => void sendMessage(text)} disabled={isSending} />
    </div>
  );
}
