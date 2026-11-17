import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { CSSProperties } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { AnnotationMode, GlobalWorkerOptions, getDocument, TextLayer, type PDFDocumentLoadingTask, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ApiError, BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentContent } from "../../api/types";
import { locatePassage, measurePassageGeometry, type TextItemBounds } from "./passageLocation";
import "./document-viewer.css";

GlobalWorkerOptions.workerSrc = workerUrl;

const DOCUMENT_ID = /^doc_[0-9a-f]{64}$/;
const MANIFEST_HASH = /^[0-9a-f]{64}$/;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.5;
const MAX_CANVAS_PIXELS = 12_000_000;

type RouteContext = {
  returnTo?: unknown;
  returnFocusId?: unknown;
  documentName?: unknown;
  citation?: unknown;
  textEvidence?: unknown;
};
type ViewerState = { kind: "loading" } | { kind: "ready"; content: Omit<BootstrapDocumentContent, "bytes">; pdf: PDFDocumentProxy } | { kind: "error"; message: string };
type RenderedTextLayer = { page: number; scale: number; rotation: number; layer: TextLayer };
type EvidenceMark = { left: number; top: number; width: number; height: number; itemIndex: number };
type CitedMatch = { label: string; documentId: string; manifestHash: string; page: number; passage: string };
type EvidenceState = { status: "none" } | { status: "matched"; marks: EvidenceMark[] } | { status: "unresolved"; reason: string };

class PageDisplayError extends Error {}

function clearCanvas(canvas: HTMLCanvasElement): void {
  canvas.width = 0;
  canvas.height = 0;
  canvas.style.width = "";
  canvas.style.height = "";
}

function renderedTextItemBounds(layer: TextLayer): Array<TextItemBounds | null> {
  return layer.textDivs.map((textDiv) => {
    if (!textDiv?.isConnected) return null;
    const rect = textDiv.getBoundingClientRect();
    const direction = getComputedStyle(textDiv).direction;
    if (direction !== "ltr" && direction !== "rtl") return null;
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      direction,
    };
  });
}

function mediaQuery(query: string): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia(query) : null;
}

function apiFailureMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local service could not open this PDF. Check that RAG-PDF is running, then try again.";
  if (error.code === "document_manifest_changed") return "This PDF changed after the citation or document list was created. Refresh Documents and open the current source.";
  if (error.code === "document_not_found" || error.code === "document_unavailable") return "The original PDF is no longer available in this source collection.";
  if (error.code === "document_changed") return "The PDF changed while it was being opened. Refresh Documents before trying again.";
  if (error.code === "document_too_large" || error.code === "document_response_too_large") return "This PDF exceeds the current viewing limit. The maximum is 16 MiB, and your configured limit may be lower.";
  if (error.code === "document_source_busy") return "The PDF is being changed or indexed. Wait for that operation to finish, then reopen it.";
  if (error.code === "document_invalid" || error.code === "document_pdf_invalid") return "This file is not a supported, readable PDF.";
  if (error.code === "authentication_required") return "Reconnect to the local document service to open this PDF.";
  if (error.kind === "network") return "The local PDF service is unavailable. Check that RAG-PDF is running and try again.";
  return "The PDF could not be opened. Return to Documents and refresh the source list.";
}

function requestedPage(search: string): { page: number; invalid: boolean; wasProvided: boolean } {
  const value = new URLSearchParams(search).get("page");
  if (value === null) return { page: 1, invalid: false, wasProvided: false };
  if (!/^[1-9][0-9]{0,5}$/.test(value)) return { page: 1, invalid: true, wasProvided: true };
  const page = Number(value);
  return { page, invalid: !Number.isSafeInteger(page), wasProvided: true };
}

function safeReturnTarget(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 2048 || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return null;
  try {
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin || !["/", "/documents"].includes(target.pathname) || target.username || target.password) return null;
    return `${target.pathname}${target.search}${target.hash}`;
  } catch { return null; }
}

export function DocumentViewerPage({ token }: { token: string | null }) {
  const { documentId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useMemo(() => new BootstrapApiClient({ token }), [token]);
  const [viewer, setViewer] = useState<ViewerState>({ kind: "loading" });
  const [renderStatus, setRenderStatus] = useState<"idle" | "rendering" | "ready" | "error">("idle");
  const [page, setPage] = useState(() => requestedPage(location.search).page);
  const [fitWidth, setFitWidth] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [smallViewport, setSmallViewport] = useState(() => mediaQuery("(max-width: 760px)")?.matches ?? false);
  const [mobileSnap, setMobileSnap] = useState(() => {
    const state = location.state && typeof location.state === "object" ? location.state as RouteContext : null;
    return state?.citation ? 30 : 55;
  });
  const [hostWidth, setHostWidth] = useState(0);
  const [pageError, setPageError] = useState<string | null>(null);
  const [renderedTextLayer, setRenderedTextLayer] = useState<RenderedTextLayer | null>(null);
  const [evidenceState, setEvidenceState] = useState<EvidenceState>({ status: "none" });
  const [showMatches, setShowMatches] = useState(false);
  const [retrievedMarks, setRetrievedMarks] = useState<Array<{ label: string; marks: EvidenceMark[] }>>([]);
  const canvasHost = useRef<HTMLDivElement>(null);
  const viewerCloseButton = useRef<HTMLButtonElement>(null);
  const pageFrame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const textLayerHost = useRef<HTMLDivElement>(null);
  const textLayerRef = useRef<TextLayer | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const pageOperationRef = useRef<Promise<void> | null>(null);
  const pageGenerationRef = useRef(0);
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  const actualScaleRef = useRef(1);
  const routeState = (location.state && typeof location.state === "object" ? location.state : {}) as RouteContext;
  const identity = new URLSearchParams(location.search).get("manifest_hash") ?? "";
  const pageRequest = requestedPage(location.search);
  const citation = routeState.citation && typeof routeState.citation === "object" ? routeState.citation as Record<string, unknown> : null;
  const citationLabel = citation && typeof citation.label === "string" && /^(?:T|I)[1-9][0-9]{0,2}$/.test(citation.label) ? citation.label : null;
  const citedPage = citation && Number.isSafeInteger(citation.page) && Number(citation.page) > 0 ? Number(citation.page) : null;
  const citedDocumentId = citation && typeof citation.documentId === "string" ? citation.documentId : "";
  const citedManifestHash = citation && typeof citation.manifestHash === "string" ? citation.manifestHash : "";
  const citedPassage = citation && typeof citation.passage === "string" ? citation.passage : "";
  const citedMatches = useMemo(() => {
    if (!Array.isArray(routeState.textEvidence)) return [];
    return routeState.textEvidence.filter((item): item is CitedMatch => Boolean(item && typeof item === "object" &&
      typeof (item as CitedMatch).label === "string" && /^(?:T)[1-9][0-9]{0,2}$/.test((item as CitedMatch).label) &&
      typeof (item as CitedMatch).documentId === "string" && typeof (item as CitedMatch).manifestHash === "string" &&
      Number.isSafeInteger((item as CitedMatch).page) && (item as CitedMatch).page > 0 &&
      typeof (item as CitedMatch).passage === "string" && (item as CitedMatch).passage.length > 0));
  }, [routeState.textEvidence]);
  const modalViewer = smallViewport && mobileSnap >= 55;

  useEffect(() => {
    const media = mediaQuery("(max-width: 760px)");
    if (!media) return;
    const update = () => setSmallViewport(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const close = useCallback(() => {
    const returnTo = safeReturnTarget(routeState.returnTo);
    const focusId = typeof routeState.returnFocusId === "string" && /^[A-Za-z][A-Za-z0-9:_-]{0,127}$/.test(routeState.returnFocusId)
      ? routeState.returnFocusId : undefined;
    navigate(returnTo ?? "/documents", { replace: true, state: focusId ? { restoreFocusId: focusId } : null });
  }, [navigate, routeState.returnFocusId, routeState.returnTo]);

  useEffect(() => {
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      close();
    };
    window.addEventListener("keydown", onEscape, true);
    return () => window.removeEventListener("keydown", onEscape, true);
  }, [close]);

  useEffect(() => {
    const node = canvasHost.current;
    if (!node) return;
    const update = () => setHostWidth(node.clientWidth);
    update();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, [viewer.kind]);

  useEffect(() => {
    if (!DOCUMENT_ID.test(documentId) || !MANIFEST_HASH.test(identity) || pageRequest.invalid) {
      setViewer({ kind: "error", message: "The PDF link is invalid or incomplete. Return to Documents and open the source again." });
      return;
    }
    const controller = new AbortController();
    let active = true;
    let loadingTask: PDFDocumentLoadingTask | null = null;
    setViewer({ kind: "loading" });
    setRenderStatus("idle");
    setPageError(null);
    // Strict Mode replays mount effects in development. Defer the read until
    // this commit turn finishes so the discarded setup is cancelled before it
    // can contend with the active setup for the source-operation lease.
    queueMicrotask(() => {
      if (!active || controller.signal.aborted) return;
      void client.getDocumentContent(documentId, identity, { signal: controller.signal }).then((content) => {
        if (!active) return;
        // The display API never creates a scripting manager; embedded actions are
        // additionally disabled in the parser and annotation renderer. PDF.js
        // 6.5.438 removed isEvalSupported; its shipped display build has no
        // dynamic eval/Function compilation path, so no unsupported flag is sent.
        const params = {
          data: content.bytes,
          enableScripting: false,
          enableXfa: false,
          useWasm: false,
          useWorkerFetch: false,
          disableRange: true,
          disableStream: true,
          disableAutoFetch: true,
          stopAtErrors: true,
          maxImageSize: 16_000_000,
          canvasMaxAreaInBytes: 16 * 1024 * 1024,
        } as Parameters<typeof getDocument>[0] & { enableScripting: false };
        loadingTask = getDocument(params);
        return loadingTask.promise.then((pdf) => {
          if (!active) { void loadingTask?.destroy(); return; }
          pdfRef.current = pdf;
          setViewer({
            kind: "ready",
            content: {
              document_id: content.document_id,
              manifest_hash: content.manifest_hash,
              document_name: content.document_name,
              page_count: content.page_count,
            },
            pdf,
          });
        });
      }).catch((error: unknown) => {
        if (active && !controller.signal.aborted) setViewer({ kind: "error", message: error instanceof Error && !(error instanceof ApiError) ? "PDF.js could not render this file. Return to Documents and try another supported PDF." : apiFailureMessage(error) });
      });
    });
    return () => {
      active = false;
      controller.abort();
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const pdf = pdfRef.current;
      pdfRef.current = null;
      const pageOperation = pageOperationRef.current;
      void (async () => {
        await pageOperation?.catch(() => undefined);
        if (pdf) await pdf.cleanup().catch(() => undefined);
        if (loadingTask) await loadingTask.destroy().catch(() => undefined);
      })();
      const currentCanvas = canvas.current;
      if (currentCanvas) clearCanvas(currentCanvas);
    };
  }, [client, documentId, identity, pageRequest.invalid]);

  useEffect(() => { setPage(pageRequest.page); }, [pageRequest.page, location.search]);

  useEffect(() => {
    if (viewer.kind !== "ready") return;
    let active = true;
    let pageTask: RenderTask | null = null;
    const textHost = textLayerHost.current;
    const frame = pageFrame.current;
    const generation = ++pageGenerationRef.current;
    const isCurrent = () => active && pageGenerationRef.current === generation;
    const previousOperation = pageOperationRef.current;
    renderTaskRef.current?.cancel();
    textLayerRef.current?.cancel();
    textLayerRef.current = null;
    setRenderedTextLayer(null);
    setEvidenceState({ status: "none" });
    setRetrievedMarks([]);
    textHost?.replaceChildren();

    if (page < 1 || page > viewer.content.page_count) {
      setPageError(`Page ${page} is not in this PDF. It has ${viewer.content.page_count} pages.`);
      setRenderStatus("error");
      const targetCanvas = canvas.current;
      if (previousOperation && targetCanvas) {
        let clearOperation!: Promise<void>;
        clearOperation = previousOperation.catch(() => undefined).then(() => {
          if (isCurrent() && canvas.current === targetCanvas) clearCanvas(targetCanvas);
        }).finally(() => {
          if (pageOperationRef.current === clearOperation) pageOperationRef.current = null;
        });
        pageOperationRef.current = clearOperation;
      } else if (targetCanvas) {
        clearCanvas(targetCanvas);
      }
      return () => { active = false; };
    }
    const targetCanvas = canvas.current;
    if (!targetCanvas || !textHost || !frame || !hostWidth) return () => { active = false; };

    // Keep the canvas mounted through errors so a later valid page can render.
    let operation!: Promise<void>;
    operation = Promise.resolve().then(async () => {
      let pdfPage: Awaited<ReturnType<PDFDocumentProxy["getPage"]>> | null = null;
      try {
        // PDF.js only permits one active render task per canvas. Wait for its
        // cancellation to settle before starting the next page operation.
        await previousOperation?.catch(() => undefined);
        if (!isCurrent()) return;
        setPageError(null);
        setRenderStatus("rendering");
        clearCanvas(targetCanvas);
        pdfPage = await viewer.pdf.getPage(page);
        if (!isCurrent()) return;

        const unit = pdfPage.getViewport({ scale: 1 });
        const desired = fitWidth ? Math.min((hostWidth - 32) / unit.width, MAX_ZOOM) : zoom;
        let scale = Math.max(MIN_ZOOM, desired);
        const area = unit.width * unit.height * scale * scale;
        if (area > MAX_CANVAS_PIXELS) scale *= Math.sqrt(MAX_CANVAS_PIXELS / area);
        actualScaleRef.current = scale;
        const viewport = pdfPage.getViewport({ scale });
        const deviceScale = Math.min(window.devicePixelRatio || 1, 1.5);
        const pixels = viewport.width * viewport.height * deviceScale * deviceScale;
        const ratio = Math.min(deviceScale, Math.sqrt(MAX_CANVAS_PIXELS / pixels));
        if (!Number.isFinite(ratio) || ratio < 0.25) {
          throw new PageDisplayError("This PDF page is too large to display safely.");
        }

        targetCanvas.width = Math.ceil(viewport.width * ratio);
        targetCanvas.height = Math.ceil(viewport.height * ratio);
        targetCanvas.style.width = `${Math.ceil(viewport.width)}px`;
        targetCanvas.style.height = `${Math.ceil(viewport.height)}px`;
        frame.style.width = `${Math.ceil(viewport.width)}px`;
        frame.style.height = `${Math.ceil(viewport.height)}px`;
        frame.style.setProperty("--scale-factor", String(scale));
        frame.style.setProperty("--total-scale-factor", String(scale));
        frame.style.setProperty("--scale-round-x", "1px");
        frame.style.setProperty("--scale-round-y", "1px");
        textHost.style.width = `${Math.ceil(viewport.width)}px`;
        textHost.style.height = `${Math.ceil(viewport.height)}px`;
        const context = targetCanvas.getContext("2d", { alpha: false });
        if (!context) throw new PageDisplayError("The browser could not create a PDF canvas.");

        pageTask = pdfPage.render({
          canvas: targetCanvas,
          canvasContext: context,
          viewport,
          annotationMode: AnnotationMode.DISABLE,
          background: "#eee8d9",
          ...(ratio === 1 ? {} : { transform: [ratio, 0, 0, ratio, 0, 0] }),
        });
        renderTaskRef.current = pageTask;
        await pageTask.promise;
        if (!isCurrent()) return;
        const textContent = await pdfPage.getTextContent();
        if (!isCurrent()) return;
        const textLayer = new TextLayer({ textContentSource: textContent, container: textHost, viewport });
        textLayerRef.current = textLayer;
        await textLayer.render();
        if (isCurrent()) {
          setRenderedTextLayer({ page, scale, rotation: viewport.rotation, layer: textLayer });
          setRenderStatus("ready");
        }
      } catch (error: unknown) {
        const cancelled = error instanceof Error && error.name === "RenderingCancelledException";
        if (isCurrent() && !cancelled) {
          const message = error instanceof PageDisplayError
            ? error.message
            : pdfPage
              ? "This page could not be rendered. Try another page or return to Documents."
              : "This page could not be read from the PDF.";
          setPageError(message);
          setRenderStatus("error");
          clearCanvas(targetCanvas);
        }
      } finally {
        try { pdfPage?.cleanup(); } catch { /* Releasing page resources is best-effort. */ }
        if (pageTask && renderTaskRef.current === pageTask) renderTaskRef.current = null;
      }
    }).finally(() => {
      if (pageOperationRef.current === operation) pageOperationRef.current = null;
    });
    pageOperationRef.current = operation;
    void operation;

    return () => {
      active = false;
      textLayerRef.current?.cancel();
      textLayerRef.current = null;
      if (pageTask) {
        pageTask.cancel();
        if (renderTaskRef.current === pageTask) renderTaskRef.current = null;
      }
    };
  }, [viewer, page, fitWidth, zoom, hostWidth]);

  useEffect(() => {
    setEvidenceState({ status: "none" });
    setRetrievedMarks([]);
    if (!citationLabel || citedPage === null || page !== citedPage || viewer.kind !== "ready" ||
      renderedTextLayer?.page !== page || !pageFrame.current) return;

    const layer = renderedTextLayer.layer;
    const textItems = layer.textContentItemsStr;
    const textItemBounds = renderedTextItemBounds(layer);
    const result = locatePassage({
      loadedDocumentId: viewer.content.document_id,
      citedDocumentId,
      loadedManifestHash: viewer.content.manifest_hash,
      citedManifestHash,
      pageNumber: citedPage,
      loadedPageNumber: renderedTextLayer.page,
      pageCount: viewer.content.page_count,
      passage: citedPassage,
      textItems,
      textItemBounds,
    });
    if (result.status === "unresolved") {
      setEvidenceState({ status: "unresolved", reason: result.reason });
      return;
    }

    const frameRect = pageFrame.current.getBoundingClientRect();
    const geometry = measurePassageGeometry(result.fragments, textItems, layer.textDivs, frameRect);
    if (geometry.status !== "matched") {
      setEvidenceState({ status: "unresolved", reason: geometry.reason });
      return;
    }
    setEvidenceState({ status: "matched", marks: geometry.marks });
    const nextRetrieved = showMatches ? citedMatches.flatMap((match) => {
      if (match.label === citationLabel || match.documentId !== viewer.content.document_id ||
        match.manifestHash !== viewer.content.manifest_hash || match.page !== page) return [];
      const located = locatePassage({
        loadedDocumentId: viewer.content.document_id,
        citedDocumentId: match.documentId,
        loadedManifestHash: viewer.content.manifest_hash,
        citedManifestHash: match.manifestHash,
        pageNumber: match.page,
        loadedPageNumber: renderedTextLayer.page,
        pageCount: viewer.content.page_count,
        passage: match.passage,
        textItems,
        textItemBounds,
      });
      if (located.status !== "matched") return [];
      const matchGeometry = measurePassageGeometry(located.fragments, textItems, layer.textDivs, frameRect);
      return matchGeometry.status === "matched"
        ? [{ label: match.label, marks: matchGeometry.marks }]
        : [];
    }) : [];
    setRetrievedMarks(nextRetrieved);
  }, [citationLabel, citedDocumentId, citedManifestHash, citedMatches, citedPage, citedPassage, page, renderedTextLayer, showMatches, viewer]);

  const changePage = (next: number) => {
    if (viewer.kind !== "ready" || next < 1 || next > viewer.content.page_count) return;
    const params = new URLSearchParams(location.search);
    params.set("page", String(next));
    navigate({ pathname: location.pathname, search: `?${params.toString()}` }, { replace: true, state: location.state });
  };

  const changeZoom = (delta: number) => {
    const base = fitWidth ? actualScaleRef.current : zoom;
    setFitWidth(false);
    setZoom(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Math.round((base + delta) * 100) / 100)));
  };

  const onViewerKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key === "Tab" && modalViewer) {
      const root = event.currentTarget;
      const items = root.querySelectorAll<HTMLElement>("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])");
      const first = items[0];
      const last = items[items.length - 1];
      if (first && last && event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (first && last && !event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      return;
    }
    if (event.target instanceof HTMLElement && /^(BUTTON|INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    if (event.key === "ArrowLeft") { event.preventDefault(); changePage(page - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); changePage(page + 1); }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); changeZoom(0.1); }
    if (event.key === "-") { event.preventDefault(); changeZoom(-0.1); }
  };

  useEffect(() => { viewerCloseButton.current?.focus(); }, []);

  const sourceContext = routeState.citation && citationLabel
    ? <aside className="pdf-viewer-context" aria-label="Citation context">
        <strong>{citationLabel}</strong>
        <span>{citedPage ? `Citation · page ${citedPage}` : "Citation page not identified"}</span>
        {citedPage === null ? <small>Opened at the start of the PDF because no page was returned.</small> : null}
        {page === citedPage && evidenceState.status === "unresolved" ? <small role="status">
          {evidenceState.reason === "document-mismatch" || evidenceState.reason === "manifest-mismatch" || evidenceState.reason === "invalid-identity"
            ? "This citation does not match the loaded PDF."
            : citationLabel.startsWith("I") && !citedPassage ? `Figure on page ${citedPage}` : `Passage on page ${citedPage}`}
        </small> : null}
      </aside>
    : null;

  const cycleMobileSnap = () => setMobileSnap((current) => current === 30 ? 55 : current === 55 ? 92 : 30);
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("reading-desk:pdf-snap", { detail: mobileSnap }));
  }, [mobileSnap]);
  return <main
    className="pdf-viewer"
    role="dialog"
    aria-modal={modalViewer || undefined}
    aria-label="PDF viewer"
    tabIndex={-1}
    onKeyDown={onViewerKeyDown}
    data-mobile-snap={mobileSnap}
    style={{ "--pdf-sheet-height": `${mobileSnap}dvh` } as CSSProperties}
  >
    <header className="pdf-viewer__header">
      {smallViewport ? <button type="button" className="pdf-viewer__snap" onClick={cycleMobileSnap} aria-label={`PDF sheet ${mobileSnap} percent. Change sheet height`}>
        <span aria-hidden="true" />{mobileSnap}%
      </button> : null}
      <button ref={viewerCloseButton} className="pdf-viewer__back" type="button" onClick={close} aria-label="Close PDF viewer">← <span>Back</span></button>
      <div className="pdf-viewer__identity">
        <span className="pdf-viewer__file-mark" aria-hidden="true">PDF</span>
        <div><h1>{viewer.kind === "ready" ? viewer.content.document_name : typeof routeState.documentName === "string" ? routeState.documentName : "PDF preview"}</h1>
          <p>{viewer.kind === "ready" ? `${viewer.content.page_count} ${viewer.content.page_count === 1 ? "page" : "pages"}` : "Original source document"}</p></div>
      </div>
      {viewer.kind === "ready" ? <nav className="pdf-viewer__controls" aria-label="PDF controls">
        <div className="pdf-viewer__pages">
          <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => changePage(page - 1)}>‹</button>
          <span aria-live="polite">{page} <span>of {viewer.content.page_count}</span></span>
          <button type="button" aria-label="Next page" disabled={page >= viewer.content.page_count} onClick={() => changePage(page + 1)}>›</button>
        </div>
        <div className="pdf-viewer__zoom">
          <button type="button" aria-label="Zoom out" disabled={!fitWidth && zoom <= MIN_ZOOM} onClick={() => changeZoom(-0.1)}>−</button>
          <span>{fitWidth ? "Fit" : `${Math.round(zoom * 100)}%`}</span>
          <button type="button" aria-label="Zoom in" disabled={!fitWidth && zoom >= MAX_ZOOM} onClick={() => changeZoom(0.1)}>+</button>
          <button type="button" className={fitWidth ? "is-active" : ""} onClick={() => setFitWidth(true)} aria-pressed={fitWidth}>Fit width</button>
          {citedMatches.length > 1 ? <button
            type="button"
            className="pdf-viewer__matches"
            data-testid="show-matches"
            aria-pressed={showMatches}
            onClick={() => setShowMatches((value) => !value)}
          >Show matches</button> : null}
        </div>
      </nav> : <button ref={viewerCloseButton} className="pdf-viewer__close" type="button" onClick={close}>Close</button>}
    </header>
    <div className="pdf-viewer__body">
      <section className="pdf-viewer__reading" aria-label="PDF reading area">
        {sourceContext}
        {viewer.kind === "loading" ? <p className="pdf-viewer__state" role="status">Loading the original PDF…</p> : null}
        {viewer.kind === "error" ? <div className="pdf-viewer__state pdf-viewer__state--error" role="alert"><p>{viewer.message}</p><button type="button" className="button button--secondary" onClick={close}>Return to Documents</button></div> : null}
        {viewer.kind === "ready" ? <div className="pdf-viewer__canvas-scroll" ref={canvasHost}>
          <div
            className="pdf-viewer__canvas-wrap"
            ref={pageFrame}
            data-testid="pdf-page-frame"
            data-document-id={viewer.content.document_id}
            data-manifest-hash={viewer.content.manifest_hash}
            data-page-number={page}
            data-viewport-scale={renderedTextLayer?.page === page ? renderedTextLayer.scale : undefined}
            data-rotation={renderedTextLayer?.page === page ? renderedTextLayer.rotation : undefined}
          >
            <canvas ref={canvas} aria-label={`PDF page ${page} of ${viewer.content.page_count}`} aria-hidden={pageError ? true : undefined} />
            <div className="textLayer pdf-viewer__text-layer" ref={textLayerHost} aria-hidden="true" />
            {evidenceState.status === "matched" ? evidenceState.marks.map((mark, index) =>
              <span
                className="pdf-viewer__evidence-mark"
                data-testid="pdf-evidence-mark"
                data-source-label={citationLabel ?? ""}
                data-item-index={mark.itemIndex}
                key={`${mark.itemIndex}-${index}`}
                style={{ left: mark.left, top: mark.top, width: mark.width, height: mark.height }}
                aria-hidden="true"
              />,
            ) : null}
            {retrievedMarks.flatMap(({ label, marks }) => marks.map((mark, index) =>
              <span
                className="pdf-viewer__retrieved-mark"
                data-testid="pdf-retrieved-mark"
                data-source-label={label}
                key={`${label}-${mark.itemIndex}-${index}`}
                style={{ left: mark.left, top: mark.top, width: mark.width, height: mark.height }}
                aria-hidden="true"
              />,
            ))}
          </div>
          {renderStatus === "rendering" ? <p className="pdf-viewer__render-status" role="status">Rendering page {page}…</p> : null}
          {pageError ? <p className="pdf-viewer__page-error" role="alert">{pageError}</p> : null}
        </div> : null}
      </section>
    </div>
  </main>;
}
