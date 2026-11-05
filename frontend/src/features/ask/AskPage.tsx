import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, WorkbenchApiClient } from "../../api/client";
import type { AskImageEvidence, AskResponse, IndexSelectionState } from "../../api/types";

interface Props { token: string | null; }

interface ImageView {
  url: string | null;
  error: boolean;
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "The request could not be completed. Check the local runtime and try again.";
  const messages: Record<string, string> = {
    index_not_selected: "Choose an index in Index Builds before asking a question.",
    ask_requires_explicit_index_selection: "This runtime has a legacy report setting. Explicitly select a compatible report in Index Builds first.",
    index_selection_stale: "The selected report or source has changed. Review compatible reports in Index Builds.",
    index_selection_incompatible: "The selected report does not match this request's source identity.",
    index_store_unavailable: "The selected index could not be opened. Review its report and storage before asking again.",
    ollama_unavailable: "The configured local answer service is unavailable.",
    generation_failed: "The local answer service could not generate a response.",
    ask_capacity_exhausted: "The local runtime is handling its maximum number of questions. Try again shortly.",
    question_too_long: "Shorten the question and try again.",
    question_invalid: "Enter a nonempty question using at most 4,000 characters.",
  };
  return messages[error.code] ?? `The local runtime returned ${error.code}.`;
}

function citationParts(answer: string, cited: string[], labels: Set<string>, onJump: (label: string) => void) {
  const pieces = answer.split(/(\[(?:T|I)[1-9][0-9]{0,2}\])/g);
  return pieces.map((piece, index) => {
    const match = /^\[((?:T|I)[1-9][0-9]{0,2})\]$/.exec(piece);
    const label = match?.[1];
    if (label && cited.includes(label) && labels.has(label)) {
      return <button className="ask-citation" type="button" key={`${label}-${index}`} onClick={() => onJump(label)} aria-label={`Jump to evidence ${label}`}>{piece}</button>;
    }
    return <span key={`answer-${index}`}>{piece}</span>;
  });
}

function pageLabel(page: number | null): string {
  return page === null ? "Page unavailable" : `Page ${page}`;
}

export function AskPage({ token }: Props) {
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const [selection, setSelection] = useState<IndexSelectionState | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [selectionLoading, setSelectionLoading] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [imageViews, setImageViews] = useState<Record<string, ImageView>>({});
  const sequence = useRef(0);
  const requestLock = useRef(false);
  const requestController = useRef<AbortController | null>(null);
  const imageControllers = useRef<Set<AbortController>>(new Set());
  const objectUrls = useRef<Set<string>>(new Set());

  const revokeImages = useCallback(() => {
    for (const controller of imageControllers.current) controller.abort();
    imageControllers.current.clear();
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    objectUrls.current.clear();
    setImageViews({});
  }, []);

  const disposeImageResources = useCallback(() => {
    for (const controller of imageControllers.current) controller.abort();
    imageControllers.current.clear();
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    objectUrls.current.clear();
  }, []);

  const refreshSelection = useCallback(async () => {
    if (!token) return;
    const current = ++sequence.current;
    setSelectionLoading(true);
    setSelectionError(null);
    try {
      const value = await client.getIndexSelection();
      if (sequence.current === current) setSelection(value);
    } catch (cause) {
      if (sequence.current === current) setSelectionError(errorMessage(cause));
    } finally {
      if (sequence.current === current) setSelectionLoading(false);
    }
  }, [client, token]);

  useEffect(() => {
    sequence.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    requestLock.current = false;
    setBusy(false);
    setSelection(null);
    setSelectionError(null);
    setAnswer(null);
    setError(null);
    setQuestion("");
    revokeImages();
    if (!token) {
      setSelectionLoading(false);
      return () => { sequence.current += 1; };
    }
    void refreshSelection();
    return () => {
      sequence.current += 1;
      requestController.current?.abort();
      disposeImageResources();
    };
  }, [token, refreshSelection, revokeImages, disposeImageResources]);

  const loadImages = useCallback(async (result: AskResponse, requestNumber: number) => {
    const views: Record<string, ImageView> = {};
    for (const evidence of result.image_evidence) {
      if (sequence.current !== requestNumber) return;
      if (!evidence.image_reference) {
        views[evidence.label] = { url: null, error: true };
        continue;
      }
      const controller = new AbortController();
      imageControllers.current.add(controller);
      try {
        const blob = await client.getAskImage(evidence.image_reference, controller.signal);
        if (sequence.current !== requestNumber) return;
        const url = URL.createObjectURL(blob);
        objectUrls.current.add(url);
        views[evidence.label] = { url, error: false };
      } catch {
        if (sequence.current !== requestNumber) return;
        views[evidence.label] = { url: null, error: true };
      } finally {
        imageControllers.current.delete(controller);
      }
    }
    if (sequence.current === requestNumber) setImageViews(views);
  }, [client]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    if (requestLock.current || !token || !normalized || normalized.length > 4000) return;
    if (selection?.selection_status !== "selected") {
      setError(selection?.selection_status === "stale"
        ? "The selected report is stale. Review it in Index Builds before asking."
        : "Select a compatible report in Index Builds before asking a question.");
      return;
    }
    requestLock.current = true;
    const requestNumber = ++sequence.current;
    const controller = new AbortController();
    requestController.current = controller;
    revokeImages();
    setBusy(true);
    setAnswer(null);
    setError(null);
    try {
      const result = await client.ask({ question: normalized }, controller.signal);
      if (sequence.current !== requestNumber) return;
      setAnswer(result);
      if (result.image_evidence.length) await loadImages(result, requestNumber);
    } catch (cause) {
      if (sequence.current !== requestNumber || (cause instanceof DOMException && cause.name === "AbortError")) return;
      setError(errorMessage(cause));
    } finally {
      if (sequence.current === requestNumber) {
        setBusy(false);
        requestLock.current = false;
        requestController.current = null;
      }
    }
  }

  const evidenceLabels = new Set([
    ...(answer?.text_evidence.map((item) => item.label) ?? []),
    ...(answer?.image_evidence.map((item) => item.label) ?? []),
  ]);
  const jumpToEvidence = (label: string) => {
    document.getElementById(`ask-evidence-${label}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const hasSelectedIndex = selection?.selection_status === "selected";
  const selectedBuildId = selection?.selection?.build_id;

  return <div className="ask-workspace">
    <header className="ask-heading">
      <div><span className="eyebrow">DOCUMENT INTELLIGENCE</span><h1>Ask your documents</h1>
        <p>Answers use the selected report and the passages or images returned by the configured retrieval strategy.</p></div>
      <div className={`ask-index-state${hasSelectedIndex ? " ask-index-state--selected" : ""}`} aria-live="polite">
        <span className="eyebrow">ACTIVE REPORT</span>
        {!token ? <><strong>Ordinary runtime disconnected</strong><small>Connect to inspect the active selection.</small></> : selectionLoading ? <span>Checking selection…</span> : hasSelectedIndex ? <><strong>{selectedBuildId ?? "Selection identity unavailable"}</strong><small>Store and model readiness have not been checked.</small></> :
          <><strong>{selection?.selection_status === "stale" ? "Selection needs review" : selection?.selection_status === "invalid" ? "Saved selection is invalid" : "No explicit selection"}</strong><Link to="/index-builds">Review Index Builds</Link></>}
      </div>
    </header>

    {selectionError ? <p className="result-error" role="alert">{selectionError}</p> : null}
    {selection?.selection_status === "invalid" ? <p className="result-error" role="alert">The saved selection is invalid and was not replaced automatically. Review runtime state before asking.</p> : null}
    {selection?.selection_status === "legacy_configured" ? <p className="ask-selection-note">A legacy report path is configured. Ask requires an explicit selection; <Link to="/index-builds">choose a compatible report</Link>.</p> : null}
    {selection?.selection_status === "stale" ? <p className="ask-selection-note" role="alert">The selected source or report changed. <Link to="/index-builds">Review compatible reports</Link> before asking.</p> : null}

    <form className="panel ask-question" onSubmit={(event) => void submit(event)}>
      <label htmlFor="ask-question">Question</label>
      <textarea id="ask-question" value={question} maxLength={4000} rows={4} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about the selected documents" aria-describedby="ask-question-help" disabled={!token || busy} />
      <div className="ask-question__footer"><p id="ask-question-help" className="muted">The configured local retrieval and answer models are used. Retrieved content is treated as untrusted evidence.</p>
        <button className="button button--primary" type="submit" disabled={!token || !hasSelectedIndex || busy || !question.trim()}>{busy ? "Working…" : "Ask"}</button></div>
      {question.length > 0 ? <small className="ask-question__count">{question.length.toLocaleString()} / 4,000 characters</small> : null}
    </form>

    {error ? <p className="result-error" role="alert">{error} {error.includes("Index Builds") ? <Link to="/index-builds">Open Index Builds</Link> : null}</p> : null}
    {busy ? <p className="ask-processing" role="status">Retrieving evidence and generating with the configured local answer model… A disconnected browser request does not guarantee the server-side operation stops.</p> : null}

    {answer ? <>
      <section className={`panel ask-answer${answer.outcome === "insufficient_evidence" ? " ask-answer--insufficient" : ""}`} aria-labelledby="ask-answer-title" aria-live="polite">
        <div className="ask-section-heading"><div><span className="eyebrow">{answer.outcome === "insufficient_evidence" ? "NO USABLE RETRIEVED EVIDENCE" : answer.model_reported_insufficient_evidence ? "MODEL REPORTS INSUFFICIENT EVIDENCE" : "GENERATED FROM SELECTED EVIDENCE"}</span><h2 id="ask-answer-title">Answer</h2></div><button className="button button--quiet" type="button" onClick={() => void refreshSelection()} disabled={!token || selectionLoading}>Refresh index status</button></div>
        {answer.outcome === "insufficient_evidence" ? <p role="status">The retrieval and context steps returned no usable text or image evidence. No answer model was called.</p> : <p className="ask-answer__text">{citationParts(answer.answer ?? "", answer.referenced_citations, evidenceLabels, jumpToEvidence)}</p>}
        {answer.model_reported_insufficient_evidence ? <p className="ask-warning" role="note">The configured model says the supplied evidence was insufficient. This is the model's response, not an independent verification.</p> : null}
        {answer.outcome === "answered" && answer.referenced_citations.length === 0 ? <p className="muted">The answer did not include a citation that could be linked to returned evidence.</p> : null}
        {answer.warnings.includes("unsupported_citation_label") ? <p className="ask-warning" role="note">The answer included a citation label that was not present in the supplied evidence. It is shown as text without a supporting link.</p> : null}
        <details className="ask-provenance"><summary>Technical provenance</summary><dl>
          <div><dt>Selected build</dt><dd><code>{answer.selected_build_id}</code></dd></div>
          <div><dt>Index fingerprint</dt><dd><code>{answer.index_fingerprint}</code></dd></div>
          <div><dt>Source manifest</dt><dd><code>{answer.document_manifest_hash}</code></dd></div>
          <div><dt>RunPlan fingerprint</dt><dd><code>{answer.run_fingerprint}</code></dd></div>
          <div><dt>Context fingerprint</dt><dd><code>{answer.context_fingerprint}</code></dd></div>
          {answer.model_identifier ? <div><dt>Answer model</dt><dd>{answer.model_identifier}</dd></div> : null}
        </dl></details>
      </section>

      {answer.text_evidence.length || answer.image_evidence.length ? <section className="ask-evidence" aria-labelledby="ask-evidence-title">
        <div className="ask-section-heading"><div><span className="eyebrow">SOURCE MATERIAL</span><h2 id="ask-evidence-title">Evidence returned for this question</h2></div><p className="muted">Citations link to these passages and images. They identify supplied evidence, not a factuality guarantee.</p></div>
        {answer.text_evidence.map((item) => <article className="panel ask-evidence-item" id={`ask-evidence-${item.label}`} key={item.label} tabIndex={-1}>
          <div className="ask-evidence-item__heading"><strong>{item.label}</strong><span>{item.source_path} · {pageLabel(item.page_number)}</span></div>
          <p>{item.passage}</p>
          {item.truncated ? <small className="muted">Passage truncated by the configured context budget.</small> : null}
          {item.record_id ? <small className="ask-evidence-item__id">Record {item.record_id}</small> : null}
        </article>)}
        {answer.image_evidence.length ? <div className="ask-image-grid">{answer.image_evidence.map((item: AskImageEvidence) => {
          const view = imageViews[item.label];
          return <figure className="panel ask-image" id={`ask-evidence-${item.label}`} key={item.label} tabIndex={-1}>
            <div className="ask-evidence-item__heading"><strong>{item.label}</strong><span>{item.source_path} · {pageLabel(item.page_number)}</span></div>
            {view?.url ? <img src={view.url} alt={item.caption ? `Retrieved image: ${item.caption}` : `Retrieved image evidence ${item.label}`} onError={() => { URL.revokeObjectURL(view.url!); objectUrls.current.delete(view.url!); setImageViews((current) => ({ ...current, [item.label]: { url: null, error: true } })); }} /> : <div className="ask-image__unavailable" role="status">{view?.error || !item.image_reference ? "Image evidence is unavailable or expired." : "Loading image evidence…"}</div>}
            {item.caption ? <figcaption>{item.caption}</figcaption> : null}
            {item.record_id ? <small className="ask-evidence-item__id">Record {item.record_id}</small> : null}
          </figure>;
        })}</div> : null}
      </section> : null}
    </> : null}

    <footer className="ask-footer-links"><Link to="/documents">Manage documents</Link><Link to="/index-builds">Inspect or select an index</Link></footer>
  </div>;
}
