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
    getPage: vi.fn(async (_pageNumber: number) => page),
    cleanup: vi.fn(async () => undefined),
  };
  const loadingTask = { promise: Promise.resolve(pdf), destroy: vi.fn(async () => undefined) };
  vi.mocked(getDocument).mockReturnValue(loadingTask as never);
  vi.spyOn(BootstrapApiClient.prototype, "getDocumentContent").mockResolvedValue({
    document_id: documentId, manifest_hash: manifestHash, document_name: "Voyager notes.pdf", page_count: pageCount, bytes,
  });
  return { page, pdf, loadingTask, renderTask };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
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
    const blankCanvas = document.querySelector(".pdf-viewer canvas");
    expect(blankCanvas).toHaveAttribute("aria-hidden", "true");
    expect(blankCanvas).toHaveAttribute("width", "0");
    expect(blankCanvas).toHaveAttribute("height", "0");
  });

  it("explains the effective viewing limit for a previously imported large PDF", async () => {
    vi.spyOn(BootstrapApiClient.prototype, "getDocumentContent")
      .mockRejectedValue(new ApiError("http", "document_too_large", 413));
    route();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This PDF exceeds the current viewing limit. The maximum is 16 MiB, and your configured limit may be lower.",
    );
  });

  it("recovers from an out-of-range URL page when the user navigates to a valid page", async () => {
    const mocks = pdfMocks(2);
    const user = userEvent.setup();
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=3`, { returnTo: "/documents" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Page 3 is not in this PDF");
    expect(document.querySelector(".pdf-viewer canvas")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Previous page" }));
    await waitFor(() => expect(screen.getByLabelText("PDF page 2 of 2")).toBeInTheDocument());
    await waitFor(() => expect(mocks.pdf.getPage).toHaveBeenCalledWith(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("Rendering page 2…")).not.toBeInTheDocument();
  });

  it("recovers from a failed page render on the next valid page", async () => {
    const mocks = pdfMocks(3);
    const failedPage = {
      getViewport: mocks.page.getViewport,
      render: vi.fn(() => ({ promise: Promise.reject(new Error("synthetic render failure")), cancel: vi.fn() })),
      cleanup: vi.fn(),
    };
    vi.mocked(mocks.pdf.getPage).mockImplementation(async (pageNumber) => pageNumber === 2 ? failedPage as never : mocks.page as never);
    const user = userEvent.setup();
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=2`, { returnTo: "/documents" });

    expect(await screen.findByRole("alert")).toHaveTextContent("This page could not be rendered");
    expect(document.querySelector(".pdf-viewer canvas")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByLabelText("PDF page 3 of 3")).toBeInTheDocument());
    await waitFor(() => expect(mocks.page.render).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("recovers from page loading and canvas context failures", async () => {
    const loadMocks = pdfMocks(3);
    vi.mocked(loadMocks.pdf.getPage).mockImplementation(async (pageNumber) => {
      if (pageNumber === 2) throw new Error("synthetic page load failure");
      return loadMocks.page as never;
    });
    const user = userEvent.setup();
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=2`, { returnTo: "/documents" });
    expect(await screen.findByRole("alert")).toHaveTextContent("This page could not be read from the PDF");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByLabelText("PDF page 3 of 3")).toBeInTheDocument());

    cleanup();
    const contextMocks = pdfMocks(3);
    vi.mocked(HTMLCanvasElement.prototype.getContext)
      .mockReturnValueOnce(null)
      .mockReturnValue({} as CanvasRenderingContext2D);
    const contextUser = userEvent.setup();
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=2`, { returnTo: "/documents" });
    expect(await screen.findByRole("alert")).toHaveTextContent("could not create a PDF canvas");
    await contextUser.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(screen.getByLabelText("PDF page 3 of 3")).toBeInTheDocument());
    await waitFor(() => expect(contextMocks.page.render).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("waits for a cancelled page render before rendering a newer page", async () => {
    const mocks = pdfMocks(3);
    const oldRender = deferred<void>();
    const pageTwoTask = { promise: oldRender.promise, cancel: vi.fn() };
    const pageTwo = {
      getViewport: mocks.page.getViewport,
      render: vi.fn(() => pageTwoTask),
      cleanup: vi.fn(),
    };
    const pageThree = {
      getViewport: mocks.page.getViewport,
      render: vi.fn(() => mocks.renderTask),
      cleanup: vi.fn(),
    };
    vi.mocked(mocks.pdf.getPage).mockImplementation(async (pageNumber) => {
      if (pageNumber === 2) return pageTwo as never;
      if (pageNumber === 3) return pageThree as never;
      return mocks.page as never;
    });
    const user = userEvent.setup();
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=1`, { returnTo: "/documents" });
    await waitFor(() => expect(screen.getByLabelText("PDF page 1 of 3")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(pageTwo.render).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(pageTwoTask.cancel).toHaveBeenCalled());
    await Promise.resolve();
    const newerRenderStartedBeforeCancellationSettled = pageThree.render.mock.calls.length > 0;
    const cancelled = new Error("render cancelled");
    cancelled.name = "RenderingCancelledException";
    oldRender.reject(cancelled);
    await waitFor(() => expect(pageThree.render).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByLabelText("PDF page 3 of 3")).toBeInTheDocument());
    expect(newerRenderStartedBeforeCancellationSettled).toBe(false);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not clean up the PDF until an in-flight page load finishes after navigation away", async () => {
    const mocks = pdfMocks(3);
    const loadingPage = deferred<never>();
    vi.mocked(mocks.pdf.getPage).mockImplementation(async (pageNumber) => {
      if (pageNumber === 2) return loadingPage.promise as never;
      return mocks.page as never;
    });
    const user = userEvent.setup();
    route(`/documents/view/${documentId}?manifest_hash=${manifestHash}&page=2`, { returnTo: "/documents" });
    await waitFor(() => expect(mocks.pdf.getPage).toHaveBeenCalledWith(2));
    await user.click(screen.getByRole("button", { name: "Return to previous page" }));
    expect(await screen.findByText("Returned to Documents")).toBeInTheDocument();
    expect(mocks.pdf.cleanup).not.toHaveBeenCalled();
    loadingPage.resolve(mocks.page as never);
    await waitFor(() => expect(mocks.pdf.cleanup).toHaveBeenCalled());
    expect(mocks.loadingTask.destroy).toHaveBeenCalled();
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
