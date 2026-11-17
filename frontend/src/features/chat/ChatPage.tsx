import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError, BootstrapApiClient, WorkbenchApiClient } from "../../api/client";
import type { AskResponse, BootstrapDocumentRecord, BootstrapDocumentsResponse, ChatModelCatalog, IndexSelectionState } from "../../api/types";
import type { GeneralChatPreferences, PreferenceLoadStatus } from "../settings/preferences";
import { resolveChatModel } from "../settings/preferences";
import { boundedChatHistory } from "./history";
import { ChatComposer } from "./ChatComposer";
import { ChatPdfAttachment } from "./ChatPdfAttachment";
import { ChatMessage } from "./ChatMessage";
import { ConversationRail } from "./ConversationRail";
import { EvidenceInspector, type SourceEvidence } from "./EvidenceInspector";
import { SamplePdfPreview } from "./SamplePdfPreview";
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

const NASA_SAMPLE_SHA256 = "2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6";
const SAMPLE_QUESTIONS = [
  "What does the rock Link tell us about water on Mars?",
  "How much power did the rover have at launch?",
  "Which crater did it land in, and what did it drill first?",
] as const;

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
  const [ollamaUnavailable, setOllamaUnavailable] = useState(false);
  const [selection, setSelection] = useState<IndexSelectionState | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [sourceInventory, setSourceInventory] = useState<BootstrapDocumentsResponse | null>(null);
  const [sampleDocument, setSampleDocument] = useState<BootstrapDocumentRecord | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [drawerMessage, setDrawerMessage] = useState<Message | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<string | null>(null);
  const [imageUrls, setImageUrls] = useState<Record<string, string>>({});
  const [answerImageUrls, setAnswerImageUrls] = useState<Record<string, string>>({});
  const [imageErrors, setImageErrors] = useState<Record<string, boolean>>({});
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const [unavailableModelNotice, setUnavailableModelNotice] = useState<string | null>(null);
  const [conversationsOpen, setConversationsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [pdfSnap, setPdfSnap] = useState(30);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const scrollRegion = useRef<HTMLDivElement>(null);
  const evidenceClose = useRef<HTMLButtonElement>(null);
  const evidenceTrigger = useRef<HTMLElement | null>(null);
  const pendingPdfFocusId = useRef<string | null>(null);
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
    setOllamaUnavailable(false);
    void client.chatModels(controller.signal).then((value) => {
      if (request === catalogRequestSequence.current) {
        setCatalog(value);
        setOllamaUnavailable(value.availability === "unavailable" || value.error_code === "ollama_unavailable");
      }
    }).catch((error: unknown) => {
      if (request === catalogRequestSequence.current && !(error instanceof DOMException && error.name === "AbortError")) {
        setCatalogError(messageFor(error));
        setOllamaUnavailable(error instanceof ApiError && error.code === "ollama_unavailable");
      }
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
  const figureMessage = [...messages].reverse().find((message) => message.role === "assistant" && message.document?.image_evidence.length);

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
    const refresh = () => {
      const request = ++serial;
      void client.getIndexSelection().then((value) => {
        if (active && request === serial) { setSelection(value); setSelectionError(null); }
      }).catch((error: unknown) => {
        if (active && request === serial && !(error instanceof DOMException && error.name === "AbortError")) { setSelection(null); setSelectionError(messageFor(error)); }
      });
    };
    refresh();
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => { active = false; window.removeEventListener("focus", onFocus); };
  }, [client, mode, visible]);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    if (!visible || !bootstrapToken) {
      if (visible && !bootstrapToken) { setSourceInventory(null); setSourceError(null); }
      return () => { active = false; };
    }
    const refresh = () => {
      controller?.abort();
      controller = new AbortController();
      const current = controller;
      void (async () => {
        const first = await bootstrapClient.listDocuments({ limit: 100, offset: 0, signal: current.signal });
        let match = first.documents.find((record) => record.sha256 === NASA_SAMPLE_SHA256) ?? null;
        for (let offset = first.limit; !match && offset < first.total; offset += 100) {
          const next = await bootstrapClient.listDocuments({ limit: 100, offset, signal: current.signal });
          if (next.manifest_hash !== first.manifest_hash || next.total !== first.total) throw new Error("document_inventory_changed");
          match = next.documents.find((record) => record.sha256 === NASA_SAMPLE_SHA256) ?? null;
        }
        if (active && controller === current) {
          setSourceInventory(first);
          setSampleDocument(match);
          setSourceError(null);
        }
      })().catch((error: unknown) => {
        if (active && controller === current && !current.signal.aborted) {
          setSourceInventory(null);
          setSampleDocument(null);
          setSourceError(messageFor(error));
        }
      });
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => { active = false; controller?.abort(); window.removeEventListener("focus", refresh); };
  }, [bootstrapClient, bootstrapToken, visible]);

  // Keep the evidence inspector across the source-viewer route only. Other
  // destinations and conversation switches dismiss it; reloads never restore it.
  useEffect(() => {
    if (location.pathname === "/") return;
    if (/^\/documents\/view\/doc_[0-9a-f]{64}$/.test(location.pathname)) {
      return;
    }
    setDrawerMessage(null);
    setActiveEvidence(null);
    releaseImages();
  }, [location.pathname, releaseImages]);

  useEffect(() => {
    const viewerRoute = /^\/documents\/view\/doc_[0-9a-f]{64}$/.test(location.pathname);
    if (!viewerRoute) { setPdfSnap(30); return; }
    const state = location.state && typeof location.state === "object"
      ? location.state as { citation?: unknown }
      : null;
    setPdfSnap(state?.citation ? 30 : 55);
    const onSnap = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (detail === 30 || detail === 55 || detail === 92) setPdfSnap(detail);
    };
    window.addEventListener("reading-desk:pdf-snap", onSnap);
    return () => window.removeEventListener("reading-desk:pdf-snap", onSnap);
  }, [location.key, location.pathname, location.state]);

  useEffect(() => {
    const state = location.state && typeof location.state === "object"
      ? location.state as { restoreFocusId?: unknown }
      : null;
    if (!isValidFocusId(state?.restoreFocusId)) return;
    const focusId = state.restoreFocusId;
    requestAnimationFrame(() => document.getElementById(focusId)?.focus());
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
  }, [location.key, location.pathname, location.search, location.state, navigate]);

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
    if (!visible || !figureMessage?.document) return;
    let active = true;
    const controllers: AbortController[] = [];
    const urls: string[] = [];
    const keys = new Set(figureMessage.document.image_evidence.map((evidence) => `${figureMessage.id}:${evidence.label}`));
    for (const evidence of figureMessage.document.image_evidence) {
      if (!evidence.image_reference) continue;
      const key = `${figureMessage.id}:${evidence.label}`;
      const controller = new AbortController();
      controllers.push(controller);
      void client.getAskImage(evidence.image_reference, controller.signal).then((blob) => {
        if (!active) return;
        const url = URL.createObjectURL(blob);
        urls.push(url);
        setAnswerImageUrls((current) => ({ ...current, [key]: url }));
      }).catch(() => {
        if (active && !controller.signal.aborted) setImageErrors((current) => ({ ...current, [key]: true }));
      });
    }
    return () => {
      active = false;
      for (const controller of controllers) controller.abort();
      for (const url of urls) URL.revokeObjectURL(url);
      setAnswerImageUrls((current) => Object.fromEntries(Object.entries(current).filter(([key]) => !keys.has(key))));
    };
  }, [client, figureMessage, visible]);

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
    if (!visible) { pendingPdfFocusId.current = null; return; }
    if (/^\/documents\/view\/doc_[0-9a-f]{64}$/.test(location.pathname)) return;
    const state = location.state && typeof location.state === "object" ? location.state as { restoreFocusId?: unknown } : null;
    const focusId = isValidFocusId(state?.restoreFocusId) ? state.restoreFocusId : pendingPdfFocusId.current;
    if (focusId) {
      const target = document.getElementById(focusId);
      if (target instanceof HTMLElement) {
        target.focus();
        const pendingId = pendingPdfFocusId.current;
        if (pendingId) {
          const timeout = window.setTimeout(() => {
            if (pendingPdfFocusId.current === pendingId) pendingPdfFocusId.current = null;
          }, 250);
          return () => window.clearTimeout(timeout);
        }
        return;
      }
      if (pendingPdfFocusId.current === focusId) pendingPdfFocusId.current = null;
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

  function openDocument(message: Message, evidence: SourceEvidence, triggerId = `citation-${message.id}-${evidence.label}`) {
    const response = message.document as AskResponse | undefined;
    if (!response || message.evidenceExpired || !response.referenced_citations.includes(evidence.label) ||
      !/^doc_[0-9a-f]{64}$/.test(evidence.document_id) || !/^[0-9a-f]{64}$/.test(response.document_manifest_hash) ||
      (evidence.page_number !== null && (!Number.isSafeInteger(evidence.page_number) || evidence.page_number < 1))) return;
    const trigger = document.getElementById(triggerId);
    pendingPdfFocusId.current = triggerId;
    const transcript = trigger?.closest<HTMLElement>(".chat-transcript");
    if (trigger && transcript) {
      const triggerRect = trigger.getBoundingClientRect();
      const transcriptRect = transcript.getBoundingClientRect();
      transcript.scrollTo({
        top: Math.max(0, transcript.scrollTop + triggerRect.top - transcriptRect.top - 16),
        behavior: "instant",
      });
    }
    const query = new URLSearchParams({ manifest_hash: response.document_manifest_hash });
    if (evidence.page_number !== null) query.set("page", String(evidence.page_number));
    navigate(`/documents/view/${evidence.document_id}?${query.toString()}`, {
      state: {
        returnTo: `${location.pathname}${location.search}`,
        returnFocusId: triggerId,
        citation: {
          label: evidence.label,
          page: evidence.page_number,
          documentId: evidence.document_id,
          manifestHash: response.document_manifest_hash,
          passage: "passage" in evidence ? evidence.passage : "",
        },
        textEvidence: response.text_evidence.map((item) => ({
          label: item.label,
          documentId: item.document_id,
          manifestHash: response.document_manifest_hash,
          page: item.page_number,
          passage: item.passage,
        })),
      },
    });
  }

  function openSampleDocument() {
    if (!sampleDocument || !sourceInventory) return;
    const query = new URLSearchParams({ manifest_hash: sourceInventory.manifest_hash, page: "1" });
    navigate(`/documents/view/${sampleDocument.document_id}?${query.toString()}`, {
      state: {
        returnTo: `${location.pathname}${location.search}`,
        returnFocusId: "sample-document-open",
        documentName: sampleDocument.relative_path.split(/[\\/]/u).at(-1) ?? "NASA factsheet",
      },
    });
  }

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || normalized.length > 4000 || !hasConversationExchangeCapacity(messages.length) || busy ||
      (mode === "general" && generalChatDisabledReason !== null) ||
      (mode === "document" && !hasIndex) ||
      (mode === "document" && (SAMPLE_QUESTIONS as readonly string[]).includes(normalized) && !sampleDocument)) return;
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

  const documentsState = selectionError || sourceError ? "unavailable" : sourceInventory?.total === 0 ? "empty" :
    selection?.selection_status === "stale" || (selection?.selection_status === "selected" && !selectedManifestMatches && sourceInventory !== null) ? "stale" :
    selection?.selection_status === "invalid" ? "invalid" : hasIndex ? "selected" : sourceInventory ? "unselected" : "checking";
  const sampleQuestion = (SAMPLE_QUESTIONS as readonly string[]).includes(question.trim());
  const sampleUnavailable = sourceError || selectionError
    ? "The local document collection could not be checked."
    : documentsState === "empty"
      ? "No PDFs imported. Add the NASA sample PDF in Library to ask these questions."
      : documentsState === "stale"
        ? "Your PDFs changed. Prepare the updated collection in Library."
        : documentsState === "invalid"
          ? "The selected index could not be verified. Review Library before asking."
          : sampleDocument === null
            ? sourceInventory === null ? "Checking whether the sample is in Library."
              : sourceInventory.total > 0
                ? `${sourceInventory.total} PDF${sourceInventory.total === 1 ? "" : "s"} in Library. Add the NASA sample PDF to use these questions.`
                : "Add the NASA sample PDF in Library to use these questions."
      : !hasIndex ? "Select a prepared index in Library before asking these sample questions." : null;
  const disabledReason = activePending ? null : busy ? "Another conversation is finishing a response. You can keep a draft here." :
    !hasConversationExchangeCapacity(messages.length) ? "This conversation has no room for another complete exchange. Start a new chat to continue." :
    mode === "general" && generalChatDisabledReason !== null ? generalChatDisabledReason :
    mode === "document" && !hasIndex ? (documentsState === "empty" ? "Add a PDF in Library and select a prepared index before asking." : "Select a compatible index in Library before sending.") :
    mode === "document" && sampleQuestion && !sampleDocument ? "Add the NASA sample PDF in Library to use these questions." : null;

  const evidenceResponse = drawerMessage?.document;
  const showPendingElsewhere = busy && !activePending;
  const isViewerRoute = /^\/documents\/view\/doc_[0-9a-f]{64}$/.test(location.pathname);
  const modelCannotBeReached = ollamaUnavailable || catalog?.availability === "unavailable" || catalog?.error_code === "ollama_unavailable";

  const composer = <ChatComposer
    question={question}
    mode={mode}
    pending={activePending}
    disabledReason={disabledReason}
    textareaRef={textarea}
    onChange={setQuestion}
    onSubmit={(event) => { void submit(event); }}
    onKeyDown={handleKeyDown}
    attachmentControl={<ChatPdfAttachment
      client={bootstrapClient}
      inventory={sourceInventory}
      inventoryLoading={visible && sourceInventory === null && sourceError === null}
      inventoryError={sourceError}
      tokenAvailable={bootstrapToken !== null}
      requestPending={busy}
      onInventoryChange={(value) => { setSourceInventory(value); setSourceError(null); }}
    />}
  />;

  return <div
    className={`reading-desk${isViewerRoute ? " reading-desk--viewer-open" : ""}`}
    style={isViewerRoute ? { "--pdf-mobile-snap": `${pdfSnap}dvh` } as CSSProperties : undefined}
  >
    <header className="reading-header">
      <Link className="reading-brand" to="/" aria-label="RAG-PDF home">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" fillRule="evenodd" d="M4 2h11l5 5v15H4V2zm2.5 2.5v15h11V8.2h-4.8V4.5H6.5z" /><path fill="var(--evidence)" d="M8 12h8v3H8z" /></svg>
        <span>RAG-PDF</span>
      </Link>
      <div className="reading-header__actions">
        <button type="button" className="reading-ghost" onClick={createConversation}>New chat</button>
        <button type="button" className="reading-ghost" aria-expanded={menuOpen} aria-controls="reading-menu" onClick={() => setMenuOpen((open) => !open)}>Menu</button>
      </div>
      {menuOpen ? <nav id="reading-menu" className="reading-menu" aria-label="Reading Desk menu">
        <button type="button" aria-pressed={mode === "general"} onClick={() => { setMenuOpen(false); selectMode("general"); }}>General Chat</button>
        <button type="button" aria-pressed={mode === "document"} onClick={() => { setMenuOpen(false); selectMode("document"); }}>Chat with PDFs</button>
        <Link to="/documents" onClick={() => setMenuOpen(false)}>Library</Link>
        <Link to="/settings" onClick={() => setMenuOpen(false)}>Settings</Link>
        <button type="button" onClick={() => { setMenuOpen(false); setConversationsOpen(true); }}>Conversations</button>
      </nav> : null}
    </header>

    {modelCannotBeReached ? <p className="reading-model-error" role="status">
      <span className="reading-model-error__dot" aria-hidden="true" />
      <span>Can't reach the model. Start Ollama, then check again.</span>
      <button type="button" onClick={checkModelAvailability} disabled={catalogChecking}>{catalogChecking ? "Checking…" : "Check again"}</button>
      <Link to="/settings">How to start it</Link>
    </p> : catalogError ? <p className="reading-service-error" role="status">Model availability could not be checked. {catalogError}</p> : null}

    {preferenceLoadStatus === "invalid" ? <p className="reading-service-error" role="status">Saved model preferences were reset to safe defaults.</p> : null}
    {unavailableModelNotice ? <p className="reading-service-error" role="status">{unavailableModelNotice}</p> : null}
    {conversations.notice ? <p className="reading-service-error" role="status">{conversations.notice}</p> : null}
    {showPendingElsewhere ? <p className="reading-service-error" role="status">A response is still finishing in another conversation.</p> : null}

    {!messages.length && mode === "document" ? <section className="reading-first-run" aria-label="Start reading">
      <section className="reading-first-run__controls">
        <p className="reading-first-run__library-status" role="status" aria-live="polite">{sampleUnavailable}</p>
        <div className="reading-suggestions" role="group" aria-label="Sample questions">
          {SAMPLE_QUESTIONS.map((suggestion) => <button type="button" key={suggestion} onClick={() => {
            if (mode !== "document") selectMode("document");
            setQuestion(suggestion);
            textarea.current?.focus();
          }}>{suggestion}</button>)}
        </div>
        <div className="reading-sample-identity">
          <span className="reading-sample-identity__icon" aria-hidden="true">PDF</span>
          <span><strong>{sampleDocument?.relative_path.split(/[\\/]/u).at(-1) ?? "Mars Science Laboratory factsheet"}</strong><small>NASA sample · separate from your Library</small></span>
          {sampleDocument ? <button id="sample-document-open" type="button" onClick={openSampleDocument}>Open PDF</button> : <Link to="/documents">Library</Link>}
        </div>
        {composer}
      </section>
      <figure className="reading-first-run__preview">
        {visible && !isViewerRoute ? <SamplePdfPreview /> : null}
      </figure>
    </section> : <section className="reading-conversation" aria-label="Conversation">
      <div
        className={`chat-transcript${messages.length ? " chat-transcript--active" : ""}`}
        ref={scrollRegion}
        role="log"
        aria-label={mode === "general" ? "General Chat messages" : "PDF conversation messages"}
        aria-live="polite"
        aria-relevant="additions"
        onScroll={(event) => { const node = event.currentTarget; stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96; }}
      >
        {messages.map((message) => <ChatMessage
          key={message.id}
          message={message}
          selectedFingerprint={selectedFingerprint}
          imageUrls={{ ...imageUrls, ...answerImageUrls }}
          onOpenEvidence={openEvidence}
          onOpenDocument={openDocument}
          onCopy={(text) => void copyAnswer(text)}
        />)}
        {activePending ? <div className="chat-thinking" role="status"><span className="chat-thinking__dots" aria-hidden="true"><i /><i /><i /></span><span>{mode === "general" ? "Waiting for the local model…" : "Retrieving evidence and preparing an answer…"}</span></div> : null}
      </div>
      {copyNotice ? <p className="chat-copy-notice" role="status">{copyNotice}</p> : null}
      {composer}
    </section>}

    {conversationsOpen ? <>
      <ConversationRail
        conversations={conversations.conversations}
        activeId={activeId}
        mode={mode}
        notice={conversations.notice}
        mobileOpen
        evidenceOpen={Boolean(drawerMessage)}
        pendingConversationId={pendingConversationId}
        onNew={createConversation}
        onSelect={selectConversation}
        onDelete={deleteConversation}
        onDismiss={() => setConversationsOpen(false)}
      />
    </> : null}

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
