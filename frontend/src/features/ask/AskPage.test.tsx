import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AskPage } from "./AskPage";
import type { AskResponse, IndexSelectionState } from "../../api/types";

const TOKEN = "ordinary-test-token";
const REFERENCE = "a".repeat(32);
const HASH = "b".repeat(64);
const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

function selection(status: IndexSelectionState["selection_status"] = "selected"): IndexSelectionState {
  const hasIdentity = status === "selected" || status === "stale";
  return {
    selection_status: status,
    revision: hasIdentity ? 1 : 0,
    selection: hasIdentity ? {
      build_id: "c".repeat(32), report_reference: `build-artifacts/${"c".repeat(32)}/build-report.json`,
      document_manifest_hash: HASH, index_fingerprint: HASH, report_sha256: HASH,
      configuration_snapshot_hash: HASH, run_fingerprint: HASH,
      report_status: "completed", selection_revision: 1,
    } : null,
    legacy_report_reference: null,
    reason_code: status === "selected" ? "selection_verified" : status === "stale" ? "index_selection_stale" : "index_not_selected",
    check_level: "report", store_checked: false,
  };
}

function answer(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    query_id: HASH, question: "What is shown?", outcome: "answered",
    answer: "A grounded answer <script>window.compromised = true</script> [T1] [I1] [T99].",
    model_reported_insufficient_evidence: false,
    failure_code: null, model_identifier: "local-answer-model", index_fingerprint: HASH,
    document_manifest_hash: HASH, run_fingerprint: HASH, context_fingerprint: HASH,
    selected_build_id: "c".repeat(32), selected_report_sha256: HASH, selection_revision: 1,
    text_evidence: [{ label: "T1", document_id: `doc_${"d".repeat(64)}`, source_path: "manual.pdf", page_number: 2,
      passage: "The source identifies the result.", record_id: "chunk-1", truncated: false }],
    image_evidence: [{ label: "I1", document_id: `doc_${"d".repeat(64)}`, source_path: "manual.pdf", page_number: 3,
      caption: "Source chart", record_id: "image-1", image_reference: REFERENCE, content_type: "image/png", size_bytes: 12 }],
    referenced_citations: ["T1", "I1"], warnings: ["unsupported_citation_label"],
    ...overrides,
  };
}

function installObjectUrlMocks() {
  const create = vi.fn(() => "blob:ask-evidence");
  const revoke = vi.fn();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
  return { create, revoke };
}

function mockFetch(result: AskResponse = answer(), selected: IndexSelectionState = selection()) {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/api/v1/index/selection")) return Response.json(selected);
    if (path.endsWith("/api/v1/ask")) return Response.json(result);
    if (path.includes("/api/v1/ask/images/")) return new Response(new Uint8Array([1, 2, 3]), { headers: { "Content-Type": "image/png" } });
    return Response.json({ error: { code: "unexpected_test_request" } }, { status: 500 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function renderPage(token: string | null = TOKEN) {
  return render(<MemoryRouter><AskPage token={token} /></MemoryRouter>);
}

afterEach(() => {
  vi.unstubAllGlobals();
  if (originalCreateObjectURL) Object.defineProperty(URL, "createObjectURL", originalCreateObjectURL);
  else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  if (originalRevokeObjectURL) Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectURL);
  else delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
});

describe("AskPage", () => {
  it("explains disconnected and unselected states and links to Index Builds", async () => {
    const fetcher = mockFetch(answer(), selection("unselected"));
    const { rerender } = renderPage(null);
    expect(screen.getByText("Ordinary runtime disconnected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
    expect(fetcher).not.toHaveBeenCalled();

    rerender(<MemoryRouter><AskPage token={TOKEN} /></MemoryRouter>);
    expect(await screen.findByText("No explicit selection")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review Index Builds" })).toHaveAttribute("href", "/index-builds");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("submits an authenticated question, renders safe answer text and loads authenticated image evidence", async () => {
    const { create, revoke } = installObjectUrlMocks();
    const fetcher = mockFetch();
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "What is shown?");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
    expect(screen.getByText("The source identifies the result.")).toBeInTheDocument();
    expect(screen.getByText("manual.pdf · Page 2")).toBeInTheDocument();
    expect(screen.getByText("A grounded answer <script>window.compromised = true</script>", { exact: false })).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByRole("button", { name: "Jump to evidence T1" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Retrieved image: Source chart" })).toHaveAttribute("src", "blob:ask-evidence");
    expect(await screen.findByText(/not present in the supplied evidence/)).toBeInTheDocument();

    const askCall = fetcher.mock.calls.find(([input]) => String(input).endsWith("/api/v1/ask"));
    expect(askCall).toBeDefined();
    expect(new Headers(askCall?.[1]?.headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
    const imageCall = fetcher.mock.calls.find(([input]) => String(input).includes("/api/v1/ask/images/"));
    expect(imageCall).toBeDefined();
    expect(new Headers(imageCall?.[1]?.headers).get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(String(imageCall?.[0])).not.toContain(TOKEN);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(window.location.href).not.toContain(TOKEN);
    expect(create).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();

    unmount();
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:ask-evidence"));
  });

  it("prevents a second question while the first request is active", async () => {
    let resolveAsk!: (value: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input) => {
      if (String(input).endsWith("/api/v1/index/selection")) return Promise.resolve(Response.json(selection()));
      if (String(input).endsWith("/api/v1/ask")) return new Promise<Response>((resolve) => { resolveAsk = resolve; });
      return Promise.resolve(Response.json({}));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "What is shown?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(screen.getByRole("button", { name: "Working…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Working…" }));
    expect(fetcher.mock.calls.filter(([input]) => String(input).endsWith("/api/v1/ask"))).toHaveLength(1);
    resolveAsk(Response.json(answer()));
    expect(await screen.findByRole("heading", { name: "Answer" })).toBeInTheDocument();
  });

  it("does not display a response that arrives after disconnect", async () => {
    let resolveAsk!: (value: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input) => {
      if (String(input).endsWith("/api/v1/index/selection")) return Promise.resolve(Response.json(selection()));
      if (String(input).endsWith("/api/v1/ask")) return new Promise<Response>((resolve) => { resolveAsk = resolve; });
      return Promise.resolve(Response.json({}));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    const { rerender } = renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "What is shown?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    rerender(<MemoryRouter><AskPage token={null} /></MemoryRouter>);
    resolveAsk(Response.json(answer()));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Answer" })).not.toBeInTheDocument());
    expect(screen.getByText("Ordinary runtime disconnected")).toBeInTheDocument();
  });

  it("shows stale selection and navigation to Documents and Index Builds", async () => {
    mockFetch(answer(), selection("stale"));
    renderPage();
    expect(await screen.findByText("Selection needs review")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("selected source or report changed");
    expect(screen.getByRole("link", { name: "Review compatible reports" })).toHaveAttribute("href", "/index-builds");
    expect(screen.getByRole("link", { name: "Manage documents" })).toHaveAttribute("href", "/documents");
    expect(screen.getByRole("link", { name: "Inspect or select an index" })).toHaveAttribute("href", "/index-builds");
  });

  it("shows image expiry and configured generation failures without raw response details", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path.endsWith("/api/v1/index/selection")) return Response.json(selection());
      if (path.endsWith("/api/v1/ask")) return Response.json(answer());
      if (path.includes("/api/v1/ask/images/")) return Response.json({ error: { code: "image_evidence_expired" } }, { status: 404 });
      return Response.json({}, { status: 500 });
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    const first = renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "What is shown?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("Image evidence is unavailable or expired.")).toBeInTheDocument();
    first.unmount();

    const failing = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      if (String(input).endsWith("/api/v1/index/selection")) return Response.json(selection());
      return Response.json({ error: { code: "ollama_unavailable" } }, { status: 503 });
    });
    vi.stubGlobal("fetch", failing);
    const other = renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "Can you answer?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("configured local answer service is unavailable");
    expect(screen.queryByText(/C:\\/)).not.toBeInTheDocument();
    other.unmount();
  });

  it("shows insufficient evidence without an answer", async () => {
    const noEvidence = answer({
      outcome: "insufficient_evidence", answer: null, failure_code: "no_usable_evidence", model_identifier: null,
      text_evidence: [], image_evidence: [], referenced_citations: [], warnings: [],
    });
    const fetcher = mockFetch(noEvidence);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "What is shown?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/No answer model was called/)).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([input]) => String(input).includes("/api/v1/ask/images/"))).toHaveLength(0);
  });

  it("labels a generated model abstention separately from missing retrieved evidence", async () => {
    const modelAbstention = answer({
      answer: "Insufficient evidence to answer this question.",
      model_reported_insufficient_evidence: true,
      text_evidence: [], image_evidence: [], referenced_citations: [],
    });
    mockFetch(modelAbstention);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("c".repeat(32));
    await user.type(screen.getByRole("textbox", { name: "Question" }), "Can you answer?");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("MODEL REPORTS INSUFFICIENT EVIDENCE")).toBeInTheDocument();
    expect(screen.getByText(/configured model says the supplied evidence was insufficient/)).toHaveAttribute("role", "note");
    expect(screen.getByText("Insufficient evidence to answer this question.")).toBeInTheDocument();
  });
});
