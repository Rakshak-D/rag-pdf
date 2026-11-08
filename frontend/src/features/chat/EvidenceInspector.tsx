import type { RefObject } from "react";
import type { AskImageEvidence, AskResponse, AskTextEvidence } from "../../api/types";
import type { Message } from "./conversations";

type SourceEvidence = AskTextEvidence | AskImageEvidence;

interface Props {
  message: Message;
  activeLabel: string | null;
  imageUrls: Record<string, string>;
  imageErrors: Record<string, boolean>;
  closeRef: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onOpenDocument: (message: Message, evidence: SourceEvidence) => void;
}

function pageName(page: number | null): string {
  return page === null ? "Page not identified" : `Page ${page}`;
}

export function EvidenceInspector({ message, activeLabel, imageUrls, imageErrors, closeRef, onClose, onOpenDocument }: Props) {
  const response = message.document as AskResponse | undefined;
  if (!response || message.evidenceExpired) return null;
  const cited = new Set(response.referenced_citations);
  return <>
    <button className="evidence-scrim" type="button" aria-label="Close source evidence" onClick={onClose} />
    <aside className="evidence-drawer" role="dialog" aria-modal="true" aria-label="Source evidence">
      <header className="evidence-drawer__header">
        <div><span className="chat-rail__eyebrow">ANSWER SOURCES</span><h2>Evidence</h2></div>
        <button ref={closeRef} type="button" aria-label="Close evidence" onClick={onClose}>×</button>
      </header>
      <p className="evidence-disclaimer">These are the passages returned for this answer.</p>
      <div className="evidence-list">
        {response.text_evidence.map((item) => <article key={item.label} className={`evidence-item${activeLabel === item.label ? " is-selected" : ""}`} id={`evidence-${message.id}-${item.label}`}>
          <div className="evidence-item__top"><strong>{item.label}</strong><span>{item.source_path} · {pageName(item.page_number)}</span></div>
          <p>{item.passage}</p>
          {item.truncated ? <small>Passage shortened for the answer context</small> : null}
          {cited.has(item.label) ? <button id={`source-open-${message.id}-${item.label}`} className="evidence-open-document" type="button" onClick={() => onOpenDocument(message, item)}>Open in document</button> : null}
        </article>)}
        {response.image_evidence.map((item) => {
          const key = `${message.id}:${item.label}`;
          return <figure key={item.label} className={`evidence-item evidence-item--image${activeLabel === item.label ? " is-selected" : ""}`} id={`evidence-${message.id}-${item.label}`}>
            <div className="evidence-item__top"><strong>{item.label}</strong><span>{item.source_path} · {pageName(item.page_number)}</span></div>
            {imageUrls[key] ? <img src={imageUrls[key]} alt={item.caption || `Retrieved image ${item.label}`} /> : <div className="evidence-image-state" role="status">{imageErrors[key] ? "Image evidence is unavailable or expired." : "Loading image evidence…"}</div>}
            {item.caption ? <figcaption>{item.caption}</figcaption> : null}
            {cited.has(item.label) ? <button id={`source-open-${message.id}-${item.label}`} className="evidence-open-document" type="button" onClick={() => onOpenDocument(message, item)}>Open in document</button> : null}
          </figure>;
        })}
      </div>
    </aside>
  </>;
}

export type { SourceEvidence };
