import type { FormEvent, KeyboardEvent, ReactNode, Ref } from "react";
import type { Mode } from "./conversations";

interface Props {
  question: string;
  mode: Mode;
  pending: boolean;
  disabledReason: string | null;
  textareaRef: Ref<HTMLTextAreaElement>;
  onChange: (value: string) => void;
  onSubmit: (event?: FormEvent<HTMLFormElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  attachmentControl?: ReactNode;
}

export function ChatComposer({ question, mode, pending, disabledReason, textareaRef, onChange, onSubmit, onKeyDown, attachmentControl }: Props) {
  const blocked = pending || disabledReason !== null;
  return <div className="chat-compose-area">
    {disabledReason ? <p className="chat-compose-hint" role="status">{disabledReason}</p> : null}
    <form className="chat-composer" onSubmit={(event) => onSubmit(event)}>
      <label className="sr-only" htmlFor="chat-question">Message</label>
      <textarea
        ref={textareaRef}
        id="chat-question"
        value={question}
        maxLength={4000}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={mode === "general" ? "Ask a question…" : "Ask about the selected PDFs…"}
        disabled={pending}
        rows={3}
        aria-describedby="chat-composer-help"
      />
      <div className="chat-composer__foot">
        <div className="chat-composer__actions">
          {attachmentControl}
          <small aria-live="off">{question.length}/4000</small>
          <button type="submit" className="chat-send" disabled={!question.trim() || blocked} aria-label="Send message" title={blocked ? disabledReason ?? "A response is in progress" : "Send message"}>
            <span aria-hidden="true">↑</span>
          </button>
        </div>
      </div>
      <p id="chat-composer-help" className="chat-composer__help">Enter to send · Shift+Enter for a new line</p>
    </form>
  </div>;
}
