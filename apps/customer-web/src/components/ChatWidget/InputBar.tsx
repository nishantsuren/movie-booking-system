import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

interface InputBarProps {
  onSend: (text: string) => void;
  disabled: boolean;
}

export default function InputBar({ onSend, disabled }: InputBarProps) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // `disabled` toggles true→false on every send/response cycle; a disabled
  // input can't hold focus, so re-focus once it's enabled again instead of
  // leaving focus wherever the browser dropped it (Design requirement: focus
  // must never drift from this box on its own).
  useEffect(() => {
    if (!disabled) {
      inputRef.current?.focus();
    }
  }, [disabled]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) {
      return;
    }
    onSend(trimmed);
    setValue("");
  }

  return (
    <form className="chat-widget-input-bar" onSubmit={handleSubmit}>
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Type a message…"
        disabled={disabled}
      />
      <button type="submit" className="primary-button" disabled={disabled || !value.trim()}>
        Send
      </button>
    </form>
  );
}
