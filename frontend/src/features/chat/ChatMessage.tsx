import type { AskResponse } from "../../api/types";
import type { Message } from "./conversations";

interface Props {
  message: Message;
  selectedFingerprint: string | null;
  onOpenEvidence: (message: Message, label: string | null, trigger: HTMLElement) => void;
  onCopy: (text: string) => void;
}

function citedText(text: string, response: AskResponse, messageId: string, onOpen: (label: string, trigger: HTMLElement) => void) {
  const permitted = new Set(response.referenced_citations);
  return text.split(/(\[[TI][0-9]{1,3}\])/g).map((part, index) => {
    const match = /^\[([TI][0-9]{1,3})\]$/.exec(part);
    const label = match?.[1];
    if (!label || !permitted.has(label)) return <span key={`${index}-${part}`}>{part}</span>;
    return <button id={`citation-${messageId}-${label}`} className="chat-citation" type="button" key={`${index}-${part}`} onClick={(event) => onOpen(label, event.currentTarget)}>{part}</button>;
  });
}

export function ChatMessage({ message, selectedFingerprint, onOpenEvidence, onCopy }: Props) {
  const response = message.document;
  const evidenceCount = (response?.text_evidence.length ?? 0) + (response?.image_evidence.length ?? 0);
  return <article className={`chat-message chat-message--${message.role}${message.error ? " chat-message--error" : ""}`}>
    <div className="chat-message__meta">
      <span className={`chat-avatar chat-avatar--${message.role}`} aria-hidden="true">{message.role === "user" ? "Y" : "R"}</span>
      <strong>{message.role === "user" ? "You" : "RAG-PDF"}</strong>
      <span>{message.mode === "general" ? "General Chat" : "Chat with PDFs"}</span>
    </div>
    <div className="chat-message__body">
      <p className="chat-message__text">{response?.outcome === "answered"
        ? citedText(message.text, response, message.id, (label, trigger) => onOpenEvidence(message, label, trigger))
        : message.text}</p>
      {message.evidenceExpired ? <p className="chat-note">Source links expired when this tab was reloaded. Ask again to retrieve current evidence.</p> : null}
      {response && selectedFingerprint && response.index_fingerprint !== selectedFingerprint ? <p className="chat-note">This answer uses the index that was selected when it was created.</p> : null}
      {response?.outcome === "insufficient_evidence" ? <p className="chat-note">No usable evidence was found for this question.</p> : null}
      {response?.model_reported_insufficient_evidence ? <p className="chat-note">The answer model reported that the supplied evidence was insufficient.</p> : null}
      {response?.warnings.includes("unsupported_citation_label") ? <p className="chat-note">One citation label did not match the returned evidence and is not linked.</p> : null}
      {response && evidenceCount > 0 ? <button className="chat-evidence-link" type="button" onClick={(event) => onOpenEvidence(message, null, event.currentTarget)}>{evidenceCount} source {evidenceCount === 1 ? "item" : "items"}</button> : null}
      {message.role === "assistant" && !message.error ? <button className="chat-copy" type="button" onClick={() => onCopy(message.text)}>Copy answer</button> : null}
    </div>
  </article>;
}
