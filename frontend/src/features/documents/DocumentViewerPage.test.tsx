import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ApiError, BootstrapApiClient } from "../../api/client";
import { DocumentViewerPage } from "./DocumentViewerPage";
import { getDocument } from "pdfjs-dist";

vi.mock("pdfjs-dist", () => ({
  AnnotationMode: { DISABLE: 0 },
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: vi.fn(),
}));

const documentId = `doc_${"a".repeat(64)}`;
const manifestHash = "b".repeat(64);
const bytes = new TextEncoder().encode("%PDF-1.7\nfixture");

function route(initial = `/documents/view/${documentId}?manifest_hash=${manifestHash}&page=2`, state: unknown = { returnTo: "/documents", returnFocusId: "origin-preview", citation: { label: "T1", page: 2 } }) {
  function RouteProbe() {
    const location = useLocation();
    return <output data-testid="route-location">{location.pathname}{location.search}</output>;
  }
  return render(<MemoryRouter initialEntries={[{ pathname: initial.split("?")[0]!, search: initial.includes("?") ? `?${initial.split("?")[1]}` : "", state }]}>
    <Routes>
      <Route path="/documents/view/:documentId" element={<><DocumentViewerPage token="bootstrap" /><RouteProbe /></>} />
      <Route path="/documents" element={<><button id="origin-preview">Preview</button><p>Returned to Documents</p><RouteProbe /></>} />
    </Routes>
  </MemoryRouter>);
}

function pdfMocks(pageCount = 3) {
  const renderTask = { promise: Promise.resolve(), cancel: vi.fn() };
  const page = {
    getViewport: vi.fn(({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale })),
    render: vi.fn(() => renderTask),
    cleanup: vi.fn(),
  };
  const pdf = {
    numPages: pageCount,
    getPage: vi.fn(async () => page),
    cleanup: vi.fn(async () => undefined),
  };
  const loadingTask = { promise: Promise.resolve(pdf), destroy: vi.fn(async () => undefined) };
  vi.mocked(getDocument).mockReturnValue(loadingTask as never);
  vi.spyOn(BootstrapApiClient.prototype, "getDocumentContent").mockResolvedValue({
    document_id: documentId, manifest_hash: manifestHash, document_name: "Voyager notes.pdf", page_count: pageCount, bytes,
  });
  return { page, pdf, loadingTask, renderTask };
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    constructor(private readonly callback: ResizeObserverCallback) { void this.callback; }
    observe() { this.callback([], this as unknown as ResizeObserver); }
    unobserve() {}
    disconnect() {}
  });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => 800 });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("DocumentViewerPage", () => {
  it("loads the authenticated source, renders its cited page, navigates, zooms, and returns", async () => {
    const mocks = pdfMocks();
    const user = userEvent.setup();
    route();

    expect(await screen.findByRole("heading", { name: "Voyager notes.pdf" })).toBeInTheDocument();
    expect(screen.getByText("Citation · page 2")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("PDF page 2 of 3")).toBeInTheDocument());
    await waitFor(() => expect(mocks.pdf.getPage).toHaveBeenCalledWith(2));
    await waitFor(() => expect(mocks.page.render).toHaveBeenCalled());
    expect(vi.mocked(getDocument).mock.calls[0]?.[0]).toMatchObject({
      enableScripting: false, enableXfa: false, useWasm: false, disableRange: true,
    });
    expect(vi.mocked(getDocument).mock.calls[0]?.[0]).not.toHaveProperty("isEvalSupported");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByLabelText("PDF page 3 of 3")).toBeInTheDocument());
    expect(screen.getByTestId("route-location")).toHaveTextContent("page=3");
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText(/%$/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Fit width" }));
    expect(screen.getByRole("button", { name: "Fit width" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Return to previous page" }));
    expect(await screen.findByText("Returned to Documents")).toBeInTheDocument();
    expect(mocks.loadingTask.destroy).toHaveBeenCalled();
    expect(mocks.pdf.cleanup).toHaveBeenCalled();
  });

  it("fails safely for stale sources and out-of-range citation pages", async () => {
    vi.spyOn(BootstrapApiClient.prototype, "getDocumentContent").mockRejectedValue(new ApiError("http", "document_manifest_changed", 409));
    route();
    expect(await screen.findByRole("alert")).toHaveTextContent("This PDF changed after the citation");
    expect(getDocument).not.toHaveBeenCalled();

    cleanup();
    pdfMocks(2);
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=9`, { returnTo: "/documents" });
    expect(await screen.findByText("Page 9 is not in this PDF. It has 2 pages.")).toBeInTheDocument();
    expect(screen.queryByLabelText(/PDF page/)).not.toBeInTheDocument();
  });

  it("closes on Escape and preserves missing citation-page information", async () => {
    pdfMocks(2);
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}`, { returnTo: "/documents", citation: { label: "T2", page: null } });
    expect(await screen.findByText("Citation page not identified")).toBeInTheDocument();
    expect(screen.getByText("Opened at the start of the PDF because no page was returned.")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("main"), { key: "Escape" });
    expect(await screen.findByText("Returned to Documents")).toBeInTheDocument();
  });
});
