import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError, BootstrapApiClient, WorkbenchApiClient } from "../../api/client";
import type { AskResponse, BootstrapDocumentsResponse, ChatModelCatalog, IndexSelectionState } from "../../api/types";
import type { GeneralChatPreferences, PreferenceLoadStatus } from "../settings/preferences";
import { resolveChatModel } from "../settings/preferences";
import { boundedChatHistory } from "./history";
import { ChatComposer } from "./ChatComposer";
import { ChatMessage } from "./ChatMessage";
import { ConversationRail } from "./ConversationRail";
import { EvidenceInspector, type SourceEvidence } from "./EvidenceInspector";
import { hasConversationExchangeCapacity, type Mode, type Message, type Conversation, type ConversationStore } from "./conversations";

interface Props {
  conversations: ConversationStore;
  visible: boolean;
  token: string | null;
  bootstrapToken: string | null;
  preferences: GeneralChatPreferences;
  preferenceLoadStatus: PreferenceLoadStatus;
  onResetUnavailableModel: () => void;
}

const errorText: Record<string, string> = {
  ollama_unavailable: "Ollama is not responding. Start the configured local Ollama service and try again.",
  chat_model_unavailable: "This model is not installed in the local Ollama service.",
  chat_model_capabilities_unavailable: "The local service could not verify this model's response capabilities.",
  ollama_capacity_unavailable: "The local model cannot accept another request right now. Try again shortly.",
  chat_capacity_exhausted: "A local chat request is already running. Wait for it to finish, then try again.",
  chat_generation_timeout: "The model request timed out. Check the conversation before retrying; the service may still be working.",
  chat_generation_failed: "The local model could not complete this response.",
  chat_response_invalid: "The local model returned an unusable response.",
  gateway_upstream_timeout: "The local request timed out. Check the conversation before retrying.",
  gateway_upstream_unavailable: "The local service could not be reached. Check that RAG-PDF is running.",
  index_not_selected: "Choose a compatible index before using Chat with PDFs.",
  index_selection_stale: "The selected index no longer matches the current PDFs. Prepare the updated collection in Documents.",
  index_selection_invalid: "The saved index selection could not be verified. Review Documents and select a compatible index.",
  ask_requires_explicit_index_selection: "Select a compatible index in Documents before asking about PDFs.",
  index_store_unavailable: "The selected index could not be opened. Review its status in Documents.",
  retrieval_failed: "Document retrieval failed. Review the selected index in Documents.",
  generation_failed: "The document answer model could not complete this response.",
};

function messageFor(error: unknown): string {
  if (error instanceof ApiError) return errorText[error.code] ?? "The local service could not complete this request.";
  return "The local service could not complete this request.";
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function conversationUrl(mode: Mode, id: string): string {
  return `/?mode=${mode}&conversation=${encodeURIComponent(id)}`;
}

function isValidFocusId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z][A-Za-z0-9:_-]{0,127}$/.test(value);
}

export function ChatPage({ token, bootstrapToken, preferences, preferenceLoadStatus, onResetUnavailableModel, conversations, visible }: Props) {
  const client = useMemo(() => new WorkbenchApiClient({ token }), [token]);
  const bootstrapClient = useMemo(() => new BootstrapApiClient({ token: bootstrapToken }), [bootstrapToken]);
  const location = useLocation();
  const navigate = useNavigate();
  const { id: activeId, mode, messages, generalTurns, question } = conversations.active;
  const { setMode, setMessages, setGeneralTurns, setQuestion } = conversations;
  const [pendingConversationId, setPendingConversationId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogChecking, setCatalogChecking] = useState(false);
  const [selection, setSelection] = useState<IndexSelectionState | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [sourceInventory, setSourceInventory] = useState<BootstrapDocumentsResponse | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [drawerMessage, setDrawerMessage] = useState<Message | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<string | null>(null);
  const [imageUrls, setImageUrls] = useState<Record<string, string>>({});
  const [imageErrors, setImageErrors] = useState<Record<string, boolean>>({});
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const [unavailableModelNotice, setUnavailableModelNotice] = useState<string | null>(null);
  const [conversationsOpen, setConversationsOpen] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const scrollRegion = useRef<HTMLDivElement>(null);
  const evidenceClose = useRef<HTMLButtonElement>(null);
  const evidenceTrigger = useRef<HTMLElement | null>(null);
  const requestSequence = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const catalogRequestSequence = useRef(0);
  const catalogRequestController = useRef<AbortController | null>(null);
  const imageControllers = useRef<Set<AbortController>>(new Set());
  const requestedImages = useRef<Set<string>>(new Set());
  const objectUrls = useRef<Set<string>>(new Set());
  const stickToBottom = useRef(true);

  const releaseImages = useCallback(() => {
    for (const controller of imageControllers.current) controller.abort();
    imageControllers.current.clear();
    requestedImages.current.clear();
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    objectUrls.current.clear();
    setImageUrls({});
    setImageErrors({});
  }, []);

  useEffect(() => {
    requestSequence.current += 1;
    requestController.current?.abort();
    requestController.current = null;
    setPendingConversationId(null);
  }, [token]);

  const modelResolution = resolveChatModel(preferences, catalog);
  const generalModel = modelResolution.model;
  const modelIsVerified = catalog?.availability === "available" && generalModel !== null && catalog.available_models.includes(generalModel);
  const generalChatDisabledReason = catalogError
    ? "Model availability could not be checked. Check again before sending."
    : catalogChecking || catalog === null
      ? "Checking local model availability…"
      : catalog.availability !== "available"
        ? "Start the configured Ollama service, then check again before sending."
        : !modelIsVerified
          ? "Choose an installed local model in Settings before sending."
          : null;

  const checkModelAvailability = useCallback(() => {
    catalogRequestController.current?.abort();
    const controller = new AbortController();
    const request = ++catalogRequestSequence.current;
    catalogRequestController.current = controller;
    setCatalogChecking(true);
    setCatalogError(null);
    void client.chatModels(controller.signal).then((value) => {
      if (request === catalogRequestSequence.current) setCatalog(value);
    }).catch((error: unknown) => {
      if (request === catalogRequestSequence.current && !(error instanceof DOMException && error.name === "AbortError")) setCatalogError(messageFor(error));
    }).finally(() => {
      if (request === catalogRequestSequence.current) {
        setCatalogChecking(false);
        catalogRequestController.current = null;
      }
    });
  }, [client]);
  const selectedFingerprint = selection?.selection_status === "selected" ? selection.selection?.index_fingerprint ?? null : null;
  const currentManifest = sourceInventory?.manifest_hash ?? null;
  const selectedManifestMatches = selection?.selection_status === "selected" && selection.selection !== null && currentManifest !== null && selection.selection.document_manifest_hash === currentManifest;
  const hasIndex = Boolean(selectedManifestMatches);
  const busy = pendingConversationId !== null;
  const activePending = pendingConversationId === activeId;

  useEffect(() => {
    if (!visible) return;
    checkModelAvailability();
    return () => {
      catalogRequestSequence.current += 1;
      catalogRequestController.current?.abort();
      catalogRequestController.current = null;
    };
  }, [checkModelAvailability, visible]);

  useEffect(() => {
    if (!visible || modelResolution.status !== "saved-model-unavailable" || !preferences.selectedModel) return;
    const previous = preferences.selectedModel;
    const fallback = catalog?.configured_model_available ? `Using configured default ${catalog.configured_model}.` : "No configured model is currently available.";
    setUnavailableModelNotice(`Saved model ${previous} is no longer installed. ${fallback}`);
    onResetUnavailableModel();
  }, [catalog, modelResolution.status, onResetUnavailableModel, preferences.selectedModel, visible]);

  useEffect(() => {
    let active = true;
    let serial = 0;
    if (!visible || mode !== "document") return () => { active = false; };
    setSelection(null);
    setSelectionError(null);
    setSourceInventory(null);
    setSourceError(null);
    const refresh = () => {
      const request = ++serial;
      const controller = new AbortController();
      void client.getIndexSelection().then((value) => {
        if (active && request === serial) { setSelection(value); setSelectionError(null); }
      }).catch((error: unknown) => {
        if (active && request === serial && !(error instanceof DOMException && error.name === "AbortError")) { setSelection(null); setSelectionError(messageFor(error)); }
      });
      void bootstrapClient.listDocuments({ limit: 1, offset: 0, signal: controller.signal }).then((value) => {
        if (active && request === serial) { setSourceInventory(value); setSourceError(null); }
      }).catch((error: unknown) => {
        if (active && request === serial && !(error instanceof DOMException && error.name === "AbortError")) { setSourceInventory(null); setSourceError(messageFor(error)); }
      });
      return controller;
    };
    let currentController = refresh();
    const onFocus = () => { currentController.abort(); currentController = refresh(); };
    window.addEventListener("focus", onFocus);
    return () => { active = false; currentController.abort(); window.removeEventListener("focus", onFocus); };
  }, [bootstrapClient, client, mode, visible]);

  // Keep the evidence inspector across the source-viewer route only. Other
  // destinations and conversation switches dismiss it; reloads never restore it.
  useEffect(() => {
    if (location.pathname === "/") return;
    if (/^\/documents\/view\/doc_[0-9a-f]{64}$/.test(location.pathname)) {
      releaseImages();
      return;
    }
    setDrawerMessage(null);
    setActiveEvidence(null);
    releaseImages();
  }, [location.pathname, releaseImages]);

  useEffect(() => {
    stickToBottom.current = true;
    setDrawerMessage(null);
    setActiveEvidence(null);
    releaseImages();
  }, [activeId, releaseImages]);

  useEffect(() => {
    const region = scrollRegion.current;
    if (region && stickToBottom.current) region.scrollTop = region.scrollHeight;
  }, [activeId, messages.length, pendingConversationId]);

  useEffect(() => () => {
    requestSequence.current += 1;
    requestController.current?.abort();
    for (const controller of imageControllers.current) controller.abort();
    for (const url of objectUrls.current) URL.revokeObjectURL(url);
    imageControllers.current.clear();
    objectUrls.current.clear();
  }, []);

  useEffect(() => {
    if (!visible || !drawerMessage?.document || !drawerMessage.document.image_evidence.length) return;
    let active = true;
    for (const evidence of drawerMessage.document.image_evidence) {
      if (!evidence.image_reference) continue;
      const key = `${drawerMessage.id}:${evidence.label}`;
      if (requestedImages.current.has(key)) continue;
      requestedImages.current.add(key);
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
    return () => { active = false; for (const controller of imageControllers.current) controller.abort(); };
  }, [client, drawerMessage, visible]);

  useEffect(() => {
    if (!drawerMessage || !visible) return;
    evidenceClose.current?.focus();
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeEvidence();
        return;
      }
      if (event.key !== "Tab") return;
      const drawer = evidenceClose.current?.closest(".evidence-drawer");
      const focusable = drawer?.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])");
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [activeEvidence, drawerMessage, visible]);

  const focusComposer = useCallback(() => { textarea.current?.focus(); }, []);
  const selectConversation = (conversation: Conversation) => {
    conversations.select(conversation.id, false);
    navigate(conversationUrl(conversation.mode, conversation.id));
  };

  const selectMode = (next: Mode) => {
    const id = setMode(next, false);
    if (id) navigate(conversationUrl(next, id));
  };

  // A conversation ID in history is authoritative. Mode-only entry points select
  // that mode's remembered conversation; a plain / preserves the current one.
  useLayoutEffect(() => {
    if (location.pathname !== "/") return;
    const params = new URLSearchParams(location.search);
    const target = conversations.conversations.find((conversation) => conversation.id === params.get("conversation"));
    if (target) {
      if (conversations.active.id !== target.id) conversations.select(target.id);
      const canonical = conversationUrl(target.mode, target.id);
      if (`${location.pathname}${location.search}` !== canonical) navigate(canonical, { replace: true, state: location.state });
      return;
    }
    if (params.has("conversation")) {
      navigate(conversationUrl(mode, conversations.active.id), { replace: true, state: null });
      return;
    }
    const requested = params.get("mode");
    if (requested === "document" || requested === "general") {
      const id = requested === mode ? conversations.active.id : setMode(requested);
      navigate(conversationUrl(id ? requested : mode, id ?? conversations.active.id), { replace: true, state: null });
    }
  }, [location.pathname, location.search, location.key, conversations.active.id, conversations.conversations, mode, navigate, setMode]);

  useEffect(() => {
    if (!visible) return;
    const state = location.state && typeof location.state === "object" ? location.state as { restoreFocusId?: unknown } : null;
    if (isValidFocusId(state?.restoreFocusId)) {
      const target = document.getElementById(state.restoreFocusId);
      if (target instanceof HTMLElement) { target.focus(); return; }
    }
    if (location.pathname === "/" && location.key !== "default") focusComposer();
  }, [focusComposer, location.key, location.pathname, location.state, visible]);

  useEffect(() => {
    if (!drawerMessage || !activeEvidence) return;
    document.getElementById(`evidence-${drawerMessage.id}-${activeEvidence}`)?.scrollIntoView?.({ block: "nearest" });
  }, [drawerMessage, activeEvidence]);

  function openEvidence(message: Message, label: string | null, trigger: HTMLElement) {
    evidenceTrigger.current = trigger;
    if (drawerMessage?.id !== message.id) releaseImages();
    setDrawerMessage(message);
    setActiveEvidence(label);
  }

  function closeEvidence() {
    setDrawerMessage(null);
    setActiveEvidence(null);
    releaseImages();
    const target = evidenceTrigger.current;
    evidenceTrigger.current = null;
    requestAnimationFrame(() => {
      if (target?.isConnected) target.focus();
      else focusComposer();
    });
  }

  function openDocument(message: Message, evidence: SourceEvidence) {
    const response = message.document as AskResponse | undefined;
    if (!response || message.evidenceExpired || !response.referenced_citations.includes(evidence.label) ||
      !/^doc_[0-9a-f]{64}$/.test(evidence.document_id) || !/^[0-9a-f]{64}$/.test(response.document_manifest_hash) ||
      (evidence.page_number !== null && (!Number.isSafeInteger(evidence.page_number) || evidence.page_number < 1))) return;
    const query = new URLSearchParams({ manifest_hash: response.document_manifest_hash });
    if (evidence.page_number !== null) query.set("page", String(evidence.page_number));
    navigate(`/documents/view/${evidence.document_id}?${query.toString()}`, {
      state: {
        returnTo: `${location.pathname}${location.search}`,
        returnFocusId: `source-open-${message.id}-${evidence.label}`,
        citation: { label: evidence.label, page: evidence.page_number },
      },
    });
  }

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || normalized.length > 4000 || !hasConversationExchangeCapacity(messages.length) || busy ||
      (mode === "general" && generalChatDisabledReason !== null) ||
      (mode === "document" && !hasIndex)) return;
    const currentMode = mode;
    const currentConversationId = activeId;
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    requestController.current = controller;
    setPendingConversationId(currentConversationId);
    const userMessage: Message = { id: newId(), role: "user", mode: currentMode, text: normalized };
    setMessages((current) => [...current, userMessage]);
    setQuestion("");
    setCopyNotice(null);
    stickToBottom.current = true;
    try {
      if (currentMode === "general") {
        const response = await client.generalChat({
          question: normalized,
          history: boundedChatHistory(generalTurns),
          ...(generalModel ? { model: generalModel } : {}),
          generation: { temperature: preferences.temperature, max_output_tokens: preferences.maxOutputTokens },
        }, controller.signal);
        if (sequence !== requestSequence.current) return;
        setMessages((current) => [...current, { id: newId(), role: "assistant", mode: "general", text: response.answer }]);
        setGeneralTurns((current) => boundedChatHistory([...current, { user: normalized, assistant: response.answer }]));
      } else {
        const response = await client.ask({ question: normalized }, controller.signal);
        if (sequence !== requestSequence.current) return;
        const answer = response.outcome === "insufficient_evidence"
          ? "No usable evidence was returned for this question. The answer model was not called."
          : response.answer ?? "The answer service returned no text.";
        setMessages((current) => [...current, { id: newId(), role: "assistant", mode: "document", text: answer, document: response }]);
      }
    } catch (error) {
      if (sequence !== requestSequence.current || (error instanceof DOMException && error.name === "AbortError")) return;
      setMessages((current) => [...current, { id: newId(), role: "assistant", mode: currentMode, text: messageFor(error), error: true }]);
    } finally {
      if (sequence === requestSequence.current) {
        requestController.current = null;
        setPendingConversationId(null);
      }
    }
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  function createConversation() {
    const id = conversations.create(mode, false);
    if (id) navigate(conversationUrl(mode, id));
  }

  function deleteConversation() {
    const title = messages.find((message) => message.role === "user")?.text.slice(0, 60) || "this conversation";
    if (!window.confirm(`Delete “${title}” and its draft from this tab? This cannot be undone.`)) return;
    const id = conversations.remove();
    const nextMode = conversations.conversations.find((conversation) => conversation.id === id)?.mode ?? mode;
    closeEvidence();
    navigate(conversationUrl(nextMode, id), { replace: true });
  }

  async function copyAnswer(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopyNotice("Answer copied");
    } catch {
      setCopyNotice("Clipboard access is unavailable in this browser");
    }
  }

  const modelStatus = catalogError ? "Model status unavailable" : catalogChecking ? "Checking model availability" : catalog?.availability === "unavailable" ? "Local model unavailable" :
    !catalog || !modelIsVerified ? "Checking installed models" : `Local model · ${generalModel}`;
  const pdfCountLabel = (count: number) => `${count} ${count === 1 ? "PDF" : "PDFs"}`;
  const documentsState = selectionError || sourceError ? "unavailable" : sourceInventory?.total === 0 ? "empty" :
    selection?.selection_status === "stale" || (selection?.selection_status === "selected" && !selectedManifestMatches && sourceInventory !== null) ? "stale" :
    selection?.selection_status === "invalid" ? "invalid" : hasIndex ? "selected" : sourceInventory ? "unselected" : "checking";
  const indexState = mode !== "document" ? null : documentsState === "empty"
    ? { title: "No PDFs imported", detail: "Add PDFs before starting a document conversation.", action: "Add PDFs", tone: "neutral" }
    : documentsState === "checking"
      ? { title: "Checking your PDF collection", detail: "The local source and index status are being checked.", action: null, tone: "neutral" }
      : documentsState === "unavailable"
        ? { title: "Document status is unavailable", detail: sourceError ?? selectionError ?? "The local service could not verify the current PDF collection.", action: "Open Documents", tone: "warning" }
        : documentsState === "stale"
          ? { title: "Your PDFs changed", detail: "Prepare an updated index before asking about the current collection.", action: "Prepare documents", tone: "warning" }
          : documentsState === "invalid"
            ? { title: "Index selection needs attention", detail: "Review Documents and choose a compatible completed report.", action: "Review Documents", tone: "warning" }
            : documentsState === "selected"
              ? { title: `${pdfCountLabel(sourceInventory?.total ?? 0)} · index selected`, detail: "Retrieval is checked when you ask; report selection alone does not verify runtime readiness.", action: null, tone: "selected" }
              : { title: `${pdfCountLabel(sourceInventory?.total ?? 0)} uploaded`, detail: "Select a compatible index before asking questions.", action: "Prepare documents", tone: "neutral" };
  const disabledReason = activePending ? null : busy ? "Another conversation is finishing a response. You can keep a draft here." :
    !hasConversationExchangeCapacity(messages.length) ? "This conversation has no room for another complete exchange. Start a new chat to continue." :
    mode === "general" && generalChatDisabledReason !== null ? generalChatDisabledReason :
    mode === "document" && !hasIndex ? (documentsState === "empty" ? "Add a PDF to start Chat with PDFs." : "Select a compatible index in Documents before sending.") : null;

  const evidenceResponse = drawerMessage?.document;
  const showPendingElsewhere = busy && !activePending;

  return <div className={`chat-workspace${drawerMessage ? " chat-workspace--with-evidence" : ""}`}>
    <ConversationRail
      conversations={conversations.conversations}
      activeId={activeId}
      mode={mode}
      notice={conversations.notice}
      mobileOpen={conversationsOpen}
      evidenceOpen={Boolean(drawerMessage)}
      pendingConversationId={pendingConversationId}
      onNew={createConversation}
      onSelect={selectConversation}
      onDelete={deleteConversation}
      onDismiss={() => setConversationsOpen(false)}
    />

    <section className="chat-main" aria-label="Chat workspace" inert={Boolean(drawerMessage)}>
      <header className="chat-heading">
        <div className="chat-heading__title">
          <h1>Chat</h1>
          <p>{mode === "general" ? "General Chat · A conversation with your local language model." : "Chat with PDFs · Ask about the selected collection."}</p>
        </div>
        <div className="chat-heading__actions">
          <button type="button" className="chat-mobile-conversations" aria-expanded={conversationsOpen} onClick={() => setConversationsOpen(true)}>Conversations</button>
          <button type="button" className="chat-new" onClick={createConversation} aria-label="New chat"><span aria-hidden="true">＋</span> New chat</button>
        </div>
      </header>

      <div className="chat-mode-row">
        <div className="chat-mode-control" role="group" aria-label="Chat mode">
          <button type="button" aria-pressed={mode === "general"} className={mode === "general" ? "is-active" : ""} onClick={() => selectMode("general")}>General Chat</button>
          <button type="button" aria-pressed={mode === "document"} className={mode === "document" ? "is-active" : ""} onClick={() => selectMode("document")}>Chat with PDFs</button>
        </div>
        <div className={`chat-context-label${mode === "general" ? (modelIsVerified ? " is-available" : " is-unavailable") : hasIndex ? " is-selected" : ""}`} aria-live="polite">
          {mode === "general" ? modelIsVerified ? `Local model · ${generalModel}` : catalog?.availability === "unavailable" ? "Local model unavailable" : "Checking model" : hasIndex ? "Index selected" : "Index needed"}
        </div>
      </div>

      {mode === "general" && unavailableModelNotice ? <p className="chat-inline-notice" role="status">{unavailableModelNotice}</p> : null}
      {mode === "general" && preferenceLoadStatus === "invalid" ? <p className="chat-inline-notice" role="status">Invalid saved preferences were reset to safe defaults.</p> : null}
      {mode === "general" && (catalog?.availability === "unavailable" || catalogError) ? <div className="chat-model-recovery" role={catalogError ? "alert" : "status"}>
        <div><strong>{catalogError ? "Model availability could not be checked" : "Ollama is unavailable"}</strong><p>{catalogError ?? "Start the configured local Ollama service, then check its availability again."}</p></div>
        <button type="button" className="button button--quiet button--small" onClick={checkModelAvailability} disabled={catalogChecking}>{catalogChecking ? "Checking…" : "Check again"}</button>
      </div> : null}
      {mode === "document" && indexState ? <div className={`chat-index-state chat-index-state--${indexState.tone}`} role={indexState.tone === "warning" ? "alert" : "status"}>
        <div><strong>{indexState.title}</strong><p>{indexState.detail}</p></div>
        {indexState.action ? <Link to="/documents" className="chat-index-action">{indexState.action}</Link> : null}
      </div> : null}
      {showPendingElsewhere ? <p className="chat-inline-notice" role="status">A response is still finishing in another conversation. It will stay with that conversation.</p> : null}

      <div
        className={`chat-transcript${messages.length ? " chat-transcript--active" : ""}`}
        ref={scrollRegion}
        role="log"
        aria-label={`${mode === "general" ? "General Chat" : "Chat with PDFs"} messages`}
        aria-live="polite"
        aria-relevant="additions"
        onScroll={(event) => { const node = event.currentTarget; stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96; }}
      >
        {!messages.length ? <div className="chat-empty">
          <div className="chat-empty__glyph" aria-hidden="true"><span /><span /><span /></div>
          <h2>{mode === "general" ? "What would you like to explore?" : "Ask a question about your PDFs"}</h2>
          <p>{mode === "general" ? "Your conversation uses the selected local model, without document retrieval." : hasIndex ? "Questions are sent to the selected index and document answer model." : "Prepare or select an index in Documents to begin."}</p>
          {mode === "general" && !catalogError && catalog?.availability !== "unavailable" ? <p className={`chat-model-status${modelIsVerified ? " is-available" : " is-unavailable"}`} role="status">
            {modelResolution.status === "configured-default-unavailable" ? `Configured model ${catalog?.configured_model ?? ""} is not installed. Choose an installed model in Settings.` : modelResolution.status === "checking" || catalogChecking ? "Checking installed models…" : modelIsVerified ? `Using ${generalModel}.` : "Choose an installed model in Settings to begin."}
          </p> : null}
        </div> : messages.map((message) => <ChatMessage key={message.id} message={message} selectedFingerprint={selectedFingerprint} onOpenEvidence={openEvidence} onCopy={(text) => void copyAnswer(text)} />)}
        {activePending ? <div className="chat-thinking" role="status"><span className="chat-thinking__dots" aria-hidden="true"><i /><i /><i /></span><span>{mode === "general" ? "Waiting for the local model…" : "Retrieving evidence and preparing an answer…"}</span></div> : null}
      </div>

      {copyNotice ? <p className="chat-copy-notice" role="status">{copyNotice}</p> : null}
      <ChatComposer
        question={question}
        mode={mode}
        modelStatus={mode === "general" ? modelStatus : hasIndex ? "PDF collection selected · retrieval checked on send" : "Document index required"}
        pending={activePending}
        disabledReason={disabledReason}
        textareaRef={textarea}
        onChange={setQuestion}
        onSubmit={(event) => { void submit(event); }}
        onKeyDown={handleKeyDown}
      />
      <p className="chat-footnote">{mode === "general" ? "General Chat is not document-grounded. " : ""}Chats are kept in this browser tab session. Reloaded document evidence expires; ask again to retrieve current sources.</p>
    </section>

    {drawerMessage && evidenceResponse ? <EvidenceInspector
      message={drawerMessage}
      activeLabel={activeEvidence}
      imageUrls={imageUrls}
      imageErrors={imageErrors}
      closeRef={evidenceClose}
      onClose={closeEvidence}
      onOpenDocument={openDocument}
    /> : null}
  </div>;
}
