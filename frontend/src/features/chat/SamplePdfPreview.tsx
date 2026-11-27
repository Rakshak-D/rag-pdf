import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, TextLayer, getDocument, type PDFDocumentLoadingTask, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { locatePassageText, measureTextFragment } from "../documents/passageLocation";

GlobalWorkerOptions.workerSrc = workerUrl;
const SAMPLE_URL = "/reading-desk/mars-science-laboratory.pdf";
const SAMPLE_SHA256 = "2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6";
const PASSAGE = "Curiosity landed successfully";
type SampleState = { kind: "loading" } | { kind: "ready"; pdf: PDFDocumentProxy; pageCount: number } | { kind: "error" };
type Mark = { left: number; top: number; width: number; height: number };

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function SamplePdfPreview() {
  const [sample, setSample] = useState<SampleState>({ kind: "loading" });
  const [hostSize, setHostSize] = useState({ width: 0, height: 0 });
  const [marks, setMarks] = useState<Mark[]>([]);
  const host = useRef<HTMLDivElement>(null);
  const pageFrame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const textHost = useRef<HTMLDivElement>(null);
  const renderTask = useRef<RenderTask | null>(null);
  const textLayer = useRef<TextLayer | null>(null);
  const renderOperation = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let active = true;
    let task: PDFDocumentLoadingTask | null = null;
    const controller = new AbortController();
    // Strict Mode cancels its first mount effect before this turn finishes.
    queueMicrotask(() => {
      if (!active) return;
      void fetch(SAMPLE_URL, { credentials: "same-origin", cache: "force-cache", signal: controller.signal }).then(async (response) => {
        if (!response.ok) throw new Error("sample_unavailable");
        const bytes = new Uint8Array(await response.arrayBuffer());
        if (await sha256(bytes) !== SAMPLE_SHA256) throw new Error("sample_identity_mismatch");
        if (!active) return null;
        const loadingTask = getDocument({
          data: bytes,
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
        } as Parameters<typeof getDocument>[0] & { enableScripting: false });
        if (!loadingTask || !loadingTask.promise || typeof loadingTask.destroy !== "function") {
          throw new Error("pdf_loading_task_unavailable");
        }
        task = loadingTask;
        return task.promise;
      }).then((pdf) => {
        if (active && pdf) setSample({ kind: "ready", pdf, pageCount: pdf.numPages });
      }).catch(() => { if (active) setSample({ kind: "error" }); });
    });
    return () => {
      active = false;
      controller.abort();
      renderTask.current?.cancel();
      textLayer.current?.cancel();
      if (task) void task.destroy().catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const update = () => {
      const { clientWidth: width, clientHeight: height } = node;
      setHostSize((current) => current.width === width && current.height === height ? current : { width, height });
    };
    update();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (sample.kind !== "ready" || hostSize.width <= 16 || hostSize.height <= 16 || !canvas.current || !pageFrame.current || !textHost.current) return;
    let active = true;
    let pageTask: RenderTask | null = null;
    let layer: TextLayer | null = null;
    let pageProxy: Awaited<ReturnType<PDFDocumentProxy["getPage"]>> | null = null;
    const previous = renderOperation.current;
    renderTask.current?.cancel();
    textLayer.current?.cancel();
    textHost.current.replaceChildren();
    setMarks([]);

    const operation = (async () => {
      try {
        // Wait for PDF.js to release this canvas after cancelling a render.
        await previous;
        if (!active) return;
        pageProxy = await sample.pdf.getPage(1);
        if (!active) return;
        const unit = pageProxy.getViewport({ scale: 1 });
        const scale = Math.min(1, (hostSize.width - 16) / unit.width, (hostSize.height - 16) / unit.height);
        const viewport = pageProxy.getViewport({ scale });
        const deviceScale = Math.min(window.devicePixelRatio || 1, 1.5);
        const targetCanvas = canvas.current!;
        const frame = pageFrame.current!;
        const textContainer = textHost.current!;
        targetCanvas.width = Math.ceil(viewport.width * deviceScale);
        targetCanvas.height = Math.ceil(viewport.height * deviceScale);
        targetCanvas.style.width = `${viewport.width}px`;
        targetCanvas.style.height = `${viewport.height}px`;
        frame.style.width = `${viewport.width}px`;
        frame.style.height = `${viewport.height}px`;
        frame.style.setProperty("--scale-factor", String(scale));
        frame.style.setProperty("--total-scale-factor", String(scale));
        frame.style.setProperty("--scale-round-x", "1px");
        frame.style.setProperty("--scale-round-y", "1px");
        textContainer.style.width = `${viewport.width}px`;
        textContainer.style.height = `${viewport.height}px`;
        const context = targetCanvas.getContext("2d", { alpha: false });
        if (!context) throw new Error("canvas_unavailable");
        pageTask = pageProxy.render({
          canvas: targetCanvas,
          canvasContext: context,
          viewport,
          background: "#eee8d9",
          ...(deviceScale === 1 ? {} : { transform: [deviceScale, 0, 0, deviceScale, 0, 0] }),
        });
        renderTask.current = pageTask;
        await pageTask.promise;
        if (!active) return;
        const textContent = await pageProxy.getTextContent();
        if (!active) return;
        layer = new TextLayer({ textContentSource: textContent, container: textContainer, viewport });
        textLayer.current = layer;
        await layer.render();
        if (!active) return;
        const match = locatePassageText(layer.textContentItemsStr, PASSAGE);
        if (match.status !== "matched") return;
        const frameRect = frame.getBoundingClientRect();
        const nextMarks = match.fragments.flatMap((fragment) => {
          const div = layer?.textDivs[fragment.itemIndex];
          if (!div) return [];
          return measureTextFragment(div, fragment, frameRect).map((rect) => ({
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }));
        });
        if (active && nextMarks.length && nextMarks.every((mark) => mark.left >= 0 && mark.top >= 0 &&
          mark.left + mark.width <= frameRect.width + 1 && mark.top + mark.height <= frameRect.height + 1)) setMarks(nextMarks);
      } catch {
        if (active) setSample({ kind: "error" });
      } finally {
        try { pageProxy?.cleanup(); } catch { /* PDF.js page cleanup is best-effort. */ }
      }
    })();
    renderOperation.current = operation;
    void operation.finally(() => {
      if (renderOperation.current === operation) renderOperation.current = null;
    });
    return () => {
      active = false;
      pageTask?.cancel();
      layer?.cancel();
      if (renderTask.current === pageTask) renderTask.current = null;
      if (textLayer.current === layer) textLayer.current = null;
    };
  }, [hostSize, sample]);

  return <div className="sample-pdf-preview" ref={host} data-testid="sample-pdf-preview">
    {sample.kind === "loading" ? <p className="sample-pdf-preview__status" role="status">Opening the sample PDF</p> : null}
    {sample.kind === "error" ? <p className="sample-pdf-preview__status" role="status">The sample PDF is unavailable.</p> : null}
    <div className="sample-pdf-preview__page" ref={pageFrame} data-document-sha256={SAMPLE_SHA256} data-page-number="1">
      <canvas ref={canvas} aria-label="NASA Mars Science Laboratory factsheet, page 1" />
      <div className="textLayer sample-pdf-preview__text-layer" ref={textHost} aria-hidden="true" />
      {marks.map((mark, index) => <span
        className="sample-pdf-preview__mark"
        data-testid="sample-pdf-mark"
        key={index}
        style={{ left: mark.left, top: mark.top, width: mark.width, height: mark.height }}
        aria-hidden="true"
      />)}
    </div>
    <p className="sample-pdf-preview__caption">NASA Mars Science Laboratory factsheet · sample page 1 of {sample.kind === "ready" ? sample.pageCount : 4}</p>
  </div>;
}
