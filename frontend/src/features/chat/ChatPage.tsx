import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, WorkbenchApiClient } from "../../api/client";
import type { AskResponse, ChatModelCatalog, ChatTurn, IndexSelectionState } from "../../api/types";
import type { GeneralChatPreferences, PreferenceLoadStatus } from "../settings/preferences";
import { resolveChatModel } from "../settings/preferences";
import { boundedChatHistory } from "./history";

type Mode = "general" | "document";
type Message = {
  id: string;
  role: "user" | "assistant";
  mode: Mode;
  text: string;
  error?: boolean;
  document?: AskResponse;
};

interface Props {
  token: string | null;
  preferences: GeneralChatPreferences;
  preferenceLoadStatus: PreferenceLoadStatus;
  onResetUnavailableModel: () => void;
}

const errorText: Record<string, string> = {
  ollama_unavailable: "Ollama is not responding. Start the configured local Ollama service and try again.",
  chat_model_unavailable: "That model is not installed in the configured local Ollama service.",
  chat_model_capabilities_unavailable: "Ollama could not verify this model's response capabilities. Check the local service and try again.",
  ollama_capacity_unavailable: "Ollama cannot accept another generation right now. Try again shortly.",
  chat_capacity_exhausted: "A local chat request is already running. Wait for it to finish, then try again.",
  chat_generation_timeout: "The local model request timed out. The service may still be finishing work; check before retrying.",
  chat_generation_failed: "The local model could not complete this response.",
  chat_response_invalid: "The local model returned an unusable response.",
  gateway_upstream_timeout: "The local request timed out. The model service may still be processing; check before retrying.",
  gateway_upstream_unavailable: "The local service could not be reached. Check that RAG-PDF is running and try again.",
  index_not_selected: "Choose a compatible index before using Document Chat.",
  index_selection_stale: "The selected index no longer matches the current documents. Review Index Builds.",
  index_selection_invalid: "The saved index selection is invalid. Review Index Builds before asking.",
  ask_requires_explicit_index_selection: "Choose a compatible report in Index Builds before using Document Chat.",
  index_store_unavailable: "The selected index could not be opened.",
  retrieval_failed: "Document retrieval failed. Review the selected index and try again.",
  generation_failed: "The answer model could not complete this response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ApiError) return errorText[error.code] ?? `The request failed (${error.code}).`;
  return "The local service could not complete this request.";
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function citationSegments(text: string, response: AskResponse, onOpen: (label: string) => void) {
  const allowed = new Set(response.referenced_citations);
  const parts = text.split(/(\[[TI][0-9]{1,3}\])/g);
  return parts.map((part, index) => {
    const match = /^\[([TI][0-9]{1,3})\]$/.exec(part);
    const label = match?.[1];
    if (!label || !allowed.has(label)) return <span key={`${index}-${part}`}>{part}</span>;
    return <button className="chat-citation" type="button" key={`${index}-${part}`} onClick={() => onOpen(label)}>{part}</button>;
  });
}

function pageName(page: number | null): string {
  return page === null ? "Page not identified" : `Page ${page}`;
}

export function ChatPage({ token, preferences, preferenceLoadStatus, onResetUnavailableModel }: Props) {
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const [searchParams] = useSearchParams();
  const [mode, setMode] = useState<Mode>(() => searchParams.get("mode") === "document" ? "document" : "general");
  const [messages, setMessages] = useState<Message[]>([]);
  const [generalTurns, setGeneralTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selection, setSelection] = useState<IndexSelectionState | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [drawerMessage, setDrawerMessage] = useState<Message | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<string | null>(null);
  const [imageUrls, setImageUrls] = useState<Record<string, string>>({});
  const [imageErrors, setImageErrors] = useState<Record<string, boolean>>({});
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const [unavailableModelNotice, setUnavailableModelNotice] = useState<string | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const scrollRegion = useRef<HTMLDivElement>(null);
  const requestSequence = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const imageControllers = useRef<Set<AbortController>>(new Set());
  const objectUrls = useRef<Set<string>>(new Set());

  const modelResolution = resolveChatModel(preferences, catalog);
  const generalModel = modelResolution.model;
  const modelIsVerified = catalog?.availability === "available" && generalModel !== null;
  const hasIndex = selection?.selection_status === "selected" && selection.selection !== null;

  const releaseImages = useCallback((clearState = true) => {
    for (const controller of imageControllers.current) controller.abort();
    imageControllers.current.clear();
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    objectUrls.current.clear();
    if (clearState) setImageUrls({});
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    void client.chatModels(controller.signal).then((value) => {
      if (active) { setCatalog(value); setCatalogError(null); }
    }).catch((error: unknown) => {
      if (active) setCatalogError(messageFor(error));
    });
    return () => { active = false; controller.abort(); };
  }, [client]);

  useEffect(() => {
    if (modelResolution.status !== "saved-model-unavailable" || !preferences.selectedModel) return;
    const previous = preferences.selectedModel;
    const fallback = catalog?.configured_model_available ? `Using configured default ${catalog.configured_model}.` :
      `Configured default ${catalog?.configured_model ?? ""} is also not installed.`;
    setUnavailableModelNotice(`Saved model ${previous} is no longer installed. ${fallback}`);
    onResetUnavailableModel();
  }, [catalog, modelResolution.status, onResetUnavailableModel, preferences.selectedModel]);

  useEffect(() => {
    let active = true;
    if (mode !== "document") {
      setSelection(null);
      setSelectionError(null);
      return () => { active = false; };
    }
    setSelection(null);
    setSelectionError(null);
    void client.getIndexSelection().then((value) => {
      if (active) setSelection(value);
    }).catch((error: unknown) => {
      if (active) setSelectionError(messageFor(error));
    });
    return () => { active = false; };
  }, [client, mode]);

  useEffect(() => {
    const region = scrollRegion.current;
    if (region) region.scrollTop = region.scrollHeight;
  }, [messages, busy]);

  useEffect(() => () => {
    requestSequence.current += 1;
    requestController.current?.abort();
    releaseImages(false);
  }, [releaseImages]);

  useEffect(() => {
    if (!drawerMessage?.document || !drawerMessage.document.image_evidence.length) return;
    let active = true;
    for (const evidence of drawerMessage.document.image_evidence) {
      if (!evidence.image_reference) continue;
      const key = `${drawerMessage.id}:${evidence.label}`;
      if (imageUrls[key] || imageErrors[key]) continue;
      const controller = new AbortController();
      imageControllers.current.add(controller);
      void client.getAskImage(evidence.image_reference, controller.signal).then((blob) => {
        if (!active) return;
        const url = URL.createObjectURL(blob);
        objectUrls.current.add(url);
        setImageUrls((current) => ({ ...current, [key]: url }));
      }).catch(() => {
        if (active && !controller.signal.aborted) setImageErrors((current) => ({ ...current, [key]: true }));
      }).finally(() => imageControllers.current.delete(controller));
    }
    return () => { active = false; };
  }, [client, drawerMessage, imageErrors, imageUrls]);

  const selectMode = (next: Mode) => {
    if (next === mode) return;
    requestSequence.current += 1;
    requestController.current?.abort();
    setBusy(false);
    setMode(next);
    setDrawerMessage(null);
    setActiveEvidence(null);
    releaseImages();
    textarea.current?.focus();
  };

  const openEvidence = (message: Message, label: string | null) => {
    setDrawerMessage(message);
    setActiveEvidence(label);
  };

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || normalized.length > 4000 || busy || (mode === "document" && !hasIndex)) return;
    const currentMode = mode;
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    requestController.current = controller;
    const id = newId();
    const userMessage: Message = { id, role: "user", mode: currentMode, text: normalized };
    setMessages((current) => [...current, userMessage]);
    setQuestion("");
    setBusy(true);
    setCopyNotice(null);
    try {
      if (currentMode === "general") {
        const response = await client.generalChat({
          question: normalized,
          history: boundedChatHistory(generalTurns),
          ...(generalModel ? { model: generalModel } : {}),
          generation: {
            temperature: preferences.temperature,
            max_output_tokens: preferences.maxOutputTokens,
          },
        }, controller.signal);
        if (sequence !== requestSequence.current) return;
        setMessages((current) => [...current, { id: newId(), role: "assistant", mode: "general", text: response.answer }]);
        setGeneralTurns((current) => boundedChatHistory([...current, { user: normalized, assistant: response.answer }]));
      } else {
        const response = await client.ask({ question: normalized }, controller.signal);
        if (sequence !== requestSequence.current) return;
        const answer = response.outcome === "insufficient_evidence"
          ? "No usable document evidence was selected for this question. The answer model was not called."
          : response.answer ?? "The answer service returned no text.";
        setMessages((current) => [...current, { id: newId(), role: "assistant", mode: "document", text: answer, document: response }]);
      }
    } catch (error) {
      if (sequence !== requestSequence.current || (error instanceof DOMException && error.name === "AbortError")) return;
      const assistant: Message = { id: newId(), role: "assistant", mode: currentMode, text: messageFor(error), error: true };
      setMessages((current) => [...current, assistant]);
    } finally {
      if (sequence === requestSequence.current) {
        requestController.current = null;
        setBusy(false);
      }
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  const clearConversation = () => {
    requestSequence.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setBusy(false);
    setQuestion("");
    setMessages([]);
    setGeneralTurns([]);
    setDrawerMessage(null);
    setActiveEvidence(null);
    setImageErrors({});
    setCopyNotice(null);
    releaseImages();
    textarea.current?.focus();
  };

  async function copyAnswer(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopyNotice("Answer copied");
    } catch {
      setCopyNotice("Clipboard access is unavailable in this browser");
    }
  }

  return <div className={`chat-workspace${drawerMessage ? " chat-workspace--with-evidence" : ""}`}>
    <section className="chat-main" aria-label="Conversation">
      <header className="chat-heading">
        <div>
          <span className="chat-kicker">RAG-PDF · LOCAL CONVERSATION</span>
          <h1>Chat</h1>
          <p>{mode === "general" ? "Talk with your local model, without document retrieval." : "Ask about the documents behind your selected index."}</p>
        </div>
        <div className="chat-heading__actions">
          {mode === "general" ? <Link className="chat-settings-link" to="/settings">Settings</Link> : null}
          <button className="chat-new" type="button" onClick={clearConversation} disabled={!messages.length && !question} aria-label="New chat">
            <span aria-hidden="true">＋</span> New chat
          </button>
        </div>
      </header>

      <div className="chat-mode-row">
        <div className="chat-mode-control" role="group" aria-label="Chat mode">
          <button type="button" disabled={busy} aria-pressed={mode === "general"} className={mode === "general" ? "is-active" : ""} onClick={() => selectMode("general")}>General</button>
          <button type="button" disabled={busy} aria-pressed={mode === "document"} className={mode === "document" ? "is-active" : ""} onClick={() => selectMode("document")}>Documents</button>
        </div>
        <div className="chat-context-label" aria-live="polite">
          {mode === "general" ? <><span className={`chat-dot${modelIsVerified ? "" : " chat-dot--muted"}`} /> General Chat{modelIsVerified ? ` · ${generalModel}` : !catalog ? " · checking models" : ""}</> : hasIndex ? <><span className="chat-dot chat-dot--rust" /> {selection?.selection?.build_id?.slice(0, 8)} selected</> : <><span className="chat-dot chat-dot--muted" /> No usable index</>}
        </div>
      </div>

      {mode === "general" && unavailableModelNotice ? <p className="chat-settings-notice" role="status">{unavailableModelNotice}</p> : null}
      {mode === "general" && preferenceLoadStatus === "invalid" ? <p className="chat-settings-notice" role="status">Saved General Chat preferences were invalid. Safe defaults are active.</p> : null}

      {mode === "document" && !hasIndex ? <div className="chat-no-index" role={selectionError ? "alert" : "status"}>
        <div className="chat-no-index__mark" aria-hidden="true">▤</div>
        <div><strong>{selectionError ? "Index status is unavailable" : selection?.selection_status === "stale" ? "Your selected index is out of date" : "Choose an index to chat with documents"}</strong>
          <p>{selectionError ?? (selection?.selection_status === "stale" ? "Review the source changes and select a compatible build." : "General Chat works without PDFs. Document Chat needs an explicitly selected, compatible index.")}</p>
          <div className="chat-inline-links"><Link to="/documents">Open Documents</Link><Link to="/index-builds">Select an index</Link></div>
        </div>
      </div> : null}

      <div className={`chat-transcript${messages.length ? " chat-transcript--active" : ""}`} ref={scrollRegion} aria-live="polite" aria-relevant="additions text">
        {!messages.length ? <div className="chat-empty">
          <div className="chat-empty__glyph" aria-hidden="true"><span /><span /><span /></div>
          <h2>{mode === "general" ? "What would you like to explore?" : "Ask a question about your PDFs"}</h2>
          <p>{mode === "general" ? "General Chat uses the configured local Ollama model. It does not read or cite your documents." : hasIndex ? "Answers use only evidence selected from your compatible index." : "Select an index to begin, or switch to General Chat."}</p>
          {mode === "general" ? <p className="chat-model-status">{catalogError ?? (catalog?.availability === "unavailable" ? "Ollama is currently unavailable. You can still open Settings to inspect model availability." : modelResolution.status === "configured-default-unavailable" ? `Configured default ${catalog?.configured_model} is not installed. Select an installed model in Settings.` : modelResolution.status === "checking" ? "Checking installed models…" : generalModel ? `Using ${generalModel}.` : "Select an installed model in Settings to begin.")}</p> : null}
        </div> : messages.map((message) => <article className={`chat-message chat-message--${message.role}${message.error ? " chat-message--error" : ""}`} key={message.id}>
          <div className="chat-message__meta"><span className={`chat-avatar chat-avatar--${message.mode}`} aria-hidden="true">{message.role === "user" ? "Y" : "R"}</span><strong>{message.role === "user" ? "You" : "RAG-PDF"}</strong><span>{message.mode === "general" ? "General Chat" : "Document Chat"}</span></div>
          <div className="chat-message__body">
            {message.document && message.document.outcome === "answered" ? <p>{citationSegments(message.text, message.document, (label) => openEvidence(message, label))}</p> : <p>{message.text}</p>}
            {message.document?.model_reported_insufficient_evidence ? <p className="chat-note">The configured model reported that the supplied evidence was insufficient.</p> : null}
            {message.document?.warnings.includes("unsupported_citation_label") ? <p className="chat-note">A citation label was not present in the supplied evidence, so it is shown without a link.</p> : null}
            {message.document?.text_evidence.length || message.document?.image_evidence.length ? <button className="chat-evidence-link" type="button" onClick={() => openEvidence(message, null)}>View {message.document.text_evidence.length + message.document.image_evidence.length} source items</button> : null}
            {message.role === "assistant" && !message.error ? <button className="chat-copy" type="button" onClick={() => void copyAnswer(message.text)}>Copy</button> : null}
          </div>
        </article>)}
        {busy ? <div className="chat-thinking" role="status"><span className="chat-thinking__dots" aria-hidden="true"><i /><i /><i /></span><span>{mode === "general" ? "Waiting for the local model…" : "Searching the selected index…"}</span></div> : null}
      </div>

      {copyNotice ? <p className="chat-copy-notice" role="status">{copyNotice}</p> : null}
      <form className="chat-composer" onSubmit={(event) => void submit(event)}>
        <label className="sr-only" htmlFor="chat-question">Message</label>
        <textarea ref={textarea} id="chat-question" value={question} maxLength={4000} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleKeyDown}
          placeholder={mode === "general" ? "Message your local model…" : "Ask about the selected documents…"} disabled={busy || (mode === "document" && !hasIndex)} rows={3} />
        <div className="chat-composer__foot">
          <span>{mode === "general" ? modelIsVerified ? `Local model · ${generalModel}` : catalog?.availability === "unavailable" ? "Ollama unavailable" : "Checking local model" : "Document answers are grounded in retrieved evidence"}</span>
          <div><small>{question.length}/4000</small><button type="submit" className="chat-send" disabled={!question.trim() || busy || (mode === "document" && !hasIndex)} aria-label="Send message"><span aria-hidden="true">↑</span></button></div>
        </div>
      </form>
      <p className="chat-footnote">{mode === "general" ? "General Chat is not document-grounded. Conversation history stays in this tab and is sent with follow-up questions." : "Each Document Chat question is evaluated independently against the selected index."} Closing or reloading this page clears the conversation.</p>
    </section>

    {drawerMessage?.document ? <>
      <button className="evidence-scrim" aria-label="Close evidence" onClick={() => { setDrawerMessage(null); releaseImages(); }} />
      <aside className="evidence-drawer" aria-label="Document evidence">
        <header><div><span className="chat-kicker">SOURCE MATERIAL</span><h2>Evidence</h2></div><button type="button" aria-label="Close evidence" onClick={() => { setDrawerMessage(null); releaseImages(); }}>×</button></header>
        <p className="evidence-disclaimer">These are the passages and images returned for this question. A citation identifies supplied evidence, not a factuality guarantee.</p>
        <div className="evidence-list">
          {drawerMessage.document.text_evidence.map((item) => <article key={item.label} className={`evidence-item${activeEvidence === item.label ? " is-selected" : ""}`} id={`evidence-${drawerMessage.id}-${item.label}`}>
            <div className="evidence-item__top"><strong>{item.label}</strong><span>{item.source_path} · {pageName(item.page_number)}</span></div><p>{item.passage}</p>
            {item.truncated ? <small>Passage shortened for the answer context</small> : null}
          </article>)}
          {drawerMessage.document.image_evidence.map((item) => {
            const key = `${drawerMessage.id}:${item.label}`;
            return <figure key={item.label} className={`evidence-item evidence-item--image${activeEvidence === item.label ? " is-selected" : ""}`} id={`evidence-${drawerMessage.id}-${item.label}`}>
              <div className="evidence-item__top"><strong>{item.label}</strong><span>{item.source_path} · {pageName(item.page_number)}</span></div>
              {imageUrls[key] ? <img src={imageUrls[key]} alt={item.caption || `Retrieved image ${item.label}`} /> : <div className="evidence-image-state" role="status">{imageErrors[key] ? "Image evidence is unavailable or expired." : "Loading authenticated image evidence…"}</div>}
              {item.caption ? <figcaption>{item.caption}</figcaption> : null}
            </figure>;
          })}
        </div>
      </aside>
    </> : null}
  </div>;
}
