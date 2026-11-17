import { useEffect, useRef, useState } from "react";
import type { AskResponse } from "../../api/types";
import type { SourceEvidence } from "./EvidenceInspector";
import type { Message } from "./conversations";

const NASA_SAMPLE_DOCUMENT_ID = "doc_2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6";
const NASA_LINK_CAPTION = "A rock outcrop called Link shows signs of being formed by the deposition of water.";

interface Props {
  message: Message;
  selectedFingerprint: string | null;
  imageUrls: Record<string, string>;
  onOpenEvidence: (message: Message, label: string | null, trigger: HTMLElement) => void;
  onOpenDocument: (message: Message, evidence: SourceEvidence, triggerId: string) => void;
  onCopy: (text: string) => void;
}

function sourceForLabel(response: AskResponse, label: string): SourceEvidence | null {
  return response.text_evidence.find((evidence) => evidence.label === label) ??
    response.image_evidence.find((evidence) => evidence.label === label) ?? null;
}

function quote(passage: string): string {
  const words = passage.trim().split(/\s+/u).filter(Boolean);
  if (words.length <= 25) return passage.trim();
  return `${words.slice(0, 25).join(" ")}…`;
}

function citedText(
  text: string,
  response: AskResponse,
  messageId: string,
  activeLabel: string | null,
  onSelect: (label: string, trigger: HTMLButtonElement) => void,
) {
  const permitted = new Set(response.referenced_citations);
  return text.split(/(\[[TI][0-9]{1,3}\])/g).map((part, index) => {
    const match = /^\[([TI][0-9]{1,3})\]$/.exec(part);
    const label = match?.[1];
    if (!label || !permitted.has(label) || !sourceForLabel(response, label)) return <span key={`${index}-${part}`}>{part}</span>;
    const id = `citation-${messageId}-${label}`;
    return <button
      id={id}
      className={`chat-citation${activeLabel === label ? " is-active" : ""}`}
      type="button"
      key={`${index}-${part}`}
      aria-expanded={activeLabel === label}
      aria-controls={`citation-popover-${messageId}`}
      onClick={(event) => onSelect(label, event.currentTarget)}
    >{part}</button>;
  });
}

export function ChatMessage({ message, selectedFingerprint, imageUrls, onOpenEvidence, onOpenDocument, onCopy }: Props) {
  const response = message.document;
  const evidenceCount = (response?.text_evidence.length ?? 0) + (response?.image_evidence.length ?? 0);
  const [activeLabel, setActiveLabel] = useState<string | null>(null);
  const activeTrigger = useRef<HTMLButtonElement | null>(null);
  const activeSource = response && activeLabel ? sourceForLabel(response, activeLabel) : null;

  useEffect(() => {
    if (!activeLabel) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setActiveLabel(null);
      requestAnimationFrame(() => activeTrigger.current?.focus());
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [activeLabel]);

  const onSelectCitation = (label: string, trigger: HTMLButtonElement) => {
    if (activeLabel === label) {
      setActiveLabel(null);
      activeTrigger.current = null;
      return;
    }
    activeTrigger.current = trigger;
    setActiveLabel(label);
  };

  const images = response?.image_evidence ?? [];
  return <article className={`chat-message chat-message--${message.role}${message.error ? " chat-message--error" : ""}`}>
    {message.role === "user" ? <p className="chat-message__user-text">{message.text}</p> : null}
    {message.role === "assistant" ? <div className="chat-message__body">
      <p className="chat-message__text">{response?.outcome === "answered"
        ? citedText(message.text, response, message.id, activeLabel, onSelectCitation)
        : message.text}</p>
      {activeSource && response ? <section className="chat-citation-popover" id={`citation-popover-${message.id}`} aria-label={`Source ${activeSource.label}`}>
        <p>{"passage" in activeSource ? quote(activeSource.passage) : quote(activeSource.caption)}</p>
        <div className="chat-citation-popover__foot">
          <span>{activeSource.page_number === null ? "Page unavailable" : `Page ${activeSource.page_number}`}</span>
          {activeSource.page_number !== null ? <button
            id={`source-open-${message.id}-${activeSource.label}`}
            type="button"
            onClick={() => onOpenDocument(message, activeSource, `citation-${message.id}-${activeSource.label}`)}
          >Open page {activeSource.page_number}</button> : <span>Page unavailable</span>}
          <button type="button" className="chat-citation-popover__close" aria-label="Close citation" onClick={() => {
            setActiveLabel(null);
            requestAnimationFrame(() => activeTrigger.current?.focus());
          }}>×</button>
        </div>
      </section> : null}
      {images.map((evidence) => {
        const imageUrl = imageUrls[`${message.id}:${evidence.label}`];
        if (!imageUrl) return null;
        const fileName = evidence.source_path.split(/[\\/]/u).at(-1) ?? evidence.source_path;
        const nasaLinkFigure = evidence.document_id === NASA_SAMPLE_DOCUMENT_ID && evidence.page_number === 2;
        return <figure className="chat-answer-figure" key={`${message.id}:${evidence.label}`}>
          <img src={imageUrl} alt={nasaLinkFigure ? NASA_LINK_CAPTION : evidence.caption || `Figure from ${fileName}`} />
          <figcaption>{nasaLinkFigure ? NASA_LINK_CAPTION : evidence.caption}<small>{fileName}{evidence.page_number === null ? "" : ` · page ${evidence.page_number}`}{nasaLinkFigure ? " · NASA/JPL-Caltech/MSSS" : ""}</small></figcaption>
        </figure>;
      })}
      {message.evidenceExpired ? <p className="chat-note">Source links expired when this tab was reloaded. Ask again to retrieve current sources.</p> : null}
      {response && selectedFingerprint && response.index_fingerprint !== selectedFingerprint ? <p className="chat-note">This answer uses the index that was selected when it was created.</p> : null}
      {response?.outcome === "insufficient_evidence" ? <p className="chat-note">No usable evidence was found for this question.</p> : null}
      {response?.model_reported_insufficient_evidence ? <p className="chat-note">The answer model reported that the supplied evidence was insufficient.</p> : null}
      {message.mode === "general" ? <p className="chat-note">This answer is not document-grounded.</p> : null}
      {response?.warnings.includes("unsupported_citation_label") ? <p className="chat-note">One citation label did not match the returned evidence and is not linked.</p> : null}
      {response && evidenceCount > 0 ? <button className="chat-evidence-link" type="button" onClick={(event) => onOpenEvidence(message, null, event.currentTarget)}>{evidenceCount} source {evidenceCount === 1 ? "item" : "items"}</button> : null}
      <button className="chat-copy" type="button" onClick={() => onCopy(message.text)}>Copy answer</button>
    </div> : null}
  </article>;
}
