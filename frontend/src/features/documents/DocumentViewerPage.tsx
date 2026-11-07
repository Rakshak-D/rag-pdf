import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { AnnotationMode, GlobalWorkerOptions, getDocument, type PDFDocumentLoadingTask, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ApiError, BootstrapApiClient } from "../../api/client";
import type { BootstrapDocumentContent } from "../../api/types";
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
};
type ViewerState = { kind: "loading" } | { kind: "ready"; content: Omit<BootstrapDocumentContent, "bytes">; pdf: PDFDocumentProxy } | { kind: "error"; message: string };

function apiFailureMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local service could not open this PDF. Check that RAG-PDF is running, then try again.";
  if (error.code === "document_manifest_changed") return "This PDF changed after the citation or document list was created. Refresh Documents and open the current source.";
  if (error.code === "document_not_found" || error.code === "document_unavailable") return "The original PDF is no longer available in this source collection.";
  if (error.code === "document_changed") return "The PDF changed while it was being opened. Refresh Documents before trying again.";
  if (error.code === "document_too_large" || error.code === "document_response_too_large") return "This PDF is larger than the 16 MiB viewing limit.";
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
  const [hostWidth, setHostWidth] = useState(0);
  const [pageError, setPageError] = useState<string | null>(null);
  const canvasHost = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  const actualScaleRef = useRef(1);
  const routeState = (location.state && typeof location.state === "object" ? location.state : {}) as RouteContext;
  const identity = new URLSearchParams(location.search).get("manifest_hash") ?? "";
  const pageRequest = requestedPage(location.search);
  const citation = routeState.citation && typeof routeState.citation === "object" ? routeState.citation as Record<string, unknown> : null;
  const citationLabel = citation && typeof citation.label === "string" && /^(?:T|I)[1-9][0-9]{0,2}$/.test(citation.label) ? citation.label : null;
  const citedPage = citation && Number.isSafeInteger(citation.page) && Number(citation.page) > 0 ? Number(citation.page) : null;

  const close = useCallback(() => {
    const returnTo = safeReturnTarget(routeState.returnTo);
    const focusId = typeof routeState.returnFocusId === "string" && /^[A-Za-z][A-Za-z0-9:_-]{0,127}$/.test(routeState.returnFocusId)
      ? routeState.returnFocusId : undefined;
    navigate(returnTo ?? "/documents", { replace: true, state: focusId ? { restoreFocusId: focusId } : null });
  }, [navigate, routeState.returnFocusId, routeState.returnTo]);

  useEffect(() => {
    const node = canvasHost.current;
    if (!node) return;
    const update = () => setHostWidth(node.clientWidth);
    update();
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
    return () => {
      active = false;
      controller.abort();
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const pdf = pdfRef.current;
      pdfRef.current = null;
      if (pdf) void pdf.cleanup().catch(() => undefined);
      if (loadingTask) void loadingTask.destroy().catch(() => undefined);
      const currentCanvas = canvas.current;
      if (currentCanvas) { currentCanvas.width = 0; currentCanvas.height = 0; }
    };
  }, [client, documentId, identity, pageRequest.invalid]);

  useEffect(() => { setPage(pageRequest.page); }, [pageRequest.page, location.search]);

  useEffect(() => {
    if (viewer.kind !== "ready") return;
    if (page < 1 || page > viewer.content.page_count) {
      setPageError(`Page ${page} is not in this PDF. It has ${viewer.content.page_count} pages.`);
      setRenderStatus("error");
      if (canvas.current) { canvas.current.width = 0; canvas.current.height = 0; }
      return;
    }
    const targetCanvas = canvas.current;
    if (!targetCanvas || !hostWidth) return;
    let active = true;
    let pageTask: RenderTask | null = null;
    setPageError(null);
    setRenderStatus("rendering");
    renderTaskRef.current?.cancel();
    void viewer.pdf.getPage(page).then((pdfPage) => {
      if (!active) { pdfPage.cleanup(); return; }
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
        setPageError("This PDF page is too large to display safely.");
        setRenderStatus("error");
        pdfPage.cleanup();
        return;
      }
      targetCanvas.width = Math.ceil(viewport.width * ratio);
      targetCanvas.height = Math.ceil(viewport.height * ratio);
      targetCanvas.style.width = `${Math.ceil(viewport.width)}px`;
      targetCanvas.style.height = `${Math.ceil(viewport.height)}px`;
      const context = targetCanvas.getContext("2d", { alpha: false });
      if (!context) {
        setPageError("The browser could not create a PDF canvas.");
        setRenderStatus("error");
        pdfPage.cleanup();
        return;
      }
      pageTask = pdfPage.render({
        canvas: targetCanvas,
        canvasContext: context,
        viewport,
        annotationMode: AnnotationMode.DISABLE,
        ...(ratio === 1 ? {} : { transform: [ratio, 0, 0, ratio, 0, 0] }),
      });
      renderTaskRef.current = pageTask;
      return pageTask.promise.then(() => {
        if (active) setRenderStatus("ready");
      }).catch((error: unknown) => {
        if (active && (!(error instanceof Error) || error.name !== "RenderingCancelledException")) {
          setPageError("This page could not be rendered. Try another page or return to Documents.");
          setRenderStatus("error");
        }
      }).finally(() => pdfPage.cleanup());
    }).catch((error: unknown) => {
      if (active) {
        setPageError("This page could not be read from the PDF.");
        setRenderStatus("error");
      }
      void error;
    });
    return () => {
      active = false;
      pageTask?.cancel();
      if (renderTaskRef.current === pageTask) renderTaskRef.current = null;
    };
  }, [viewer, page, fitWidth, zoom, hostWidth]);

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
    if (event.target instanceof HTMLElement && /^(BUTTON|INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    if (event.key === "ArrowLeft") { event.preventDefault(); changePage(page - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); changePage(page + 1); }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); changeZoom(0.1); }
    if (event.key === "-") { event.preventDefault(); changeZoom(-0.1); }
  };

  const sourceContext = routeState.citation && citationLabel
    ? <aside className="pdf-viewer-context" aria-label="Citation context">
        <strong>{citationLabel}</strong>
        <span>{citedPage ? `Citation · page ${citedPage}` : "Citation page not identified"}</span>
        {citedPage === null ? <small>Opened at the start of the PDF because no page was returned.</small> : null}
      </aside>
    : null;

  return <main className="pdf-viewer" onKeyDown={onViewerKeyDown}>
    <header className="pdf-viewer__header">
      <button className="pdf-viewer__back" type="button" onClick={close} aria-label="Return to previous page">← <span>Back</span></button>
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
        </div>
      </nav> : <button className="pdf-viewer__close" type="button" onClick={close}>Close</button>}
    </header>
    <div className="pdf-viewer__body">
      <section className="pdf-viewer__reading" aria-label="PDF reading area">
        {sourceContext}
        {viewer.kind === "loading" ? <p className="pdf-viewer__state" role="status">Loading the original PDF…</p> : null}
        {viewer.kind === "error" ? <div className="pdf-viewer__state pdf-viewer__state--error" role="alert"><p>{viewer.message}</p><button type="button" className="button button--secondary" onClick={close}>Return to Documents</button></div> : null}
        {viewer.kind === "ready" ? <div className="pdf-viewer__canvas-scroll" ref={canvasHost}>
          <div className="pdf-viewer__canvas-wrap">
            {!pageError ? <canvas ref={canvas} aria-label={`PDF page ${page} of ${viewer.content.page_count}`} /> : null}
          </div>
          {renderStatus === "rendering" ? <p className="pdf-viewer__render-status" role="status">Rendering page {page}…</p> : null}
          {pageError ? <p className="pdf-viewer__page-error" role="alert">{pageError}</p> : null}
        </div> : null}
      </section>
    </div>
  </main>;
}
