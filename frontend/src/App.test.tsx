import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ComponentType } from "react";

let App: ComponentType = (() => null) as ComponentType;
beforeAll(async () => {
  vi.stubEnv("VITE_RAG_LOCAL_GATEWAY", "true");
  App = (await import("./App")).default;
});
afterAll(() => vi.unstubAllEnvs());
const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
  if (originalCreateObjectURL) Object.defineProperty(URL, "createObjectURL", originalCreateObjectURL);
  else delete (URL as unknown as { createObjectURL?: unknown }).createObjectURL;
  if (originalRevokeObjectURL) Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectURL);
  else delete (URL as unknown as { revokeObjectURL?: unknown }).revokeObjectURL;
  if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
  else delete (navigator as unknown as { clipboard?: unknown }).clipboard;
});

const health = { status: "ok", api_version: "v1" };
const runtime = { components: [] };
const models = {
  availability: "available",
  configured_model: "gemma4:e2b",
  configured_model_available: true,
  available_models: ["gemma4:e2b", "qwen2.5:3b"],
  error_code: null,
};
const unselected = {
  selection_status: "unselected", revision: 0, selection: null,
  legacy_report_reference: null, reason_code: "index_not_selected",
  check_level: "report", store_checked: false,
};
const selected = {
  selection_status: "selected", revision: 1,
  selection: {
    build_id: "c".repeat(32), report_reference: `build-artifacts/${"c".repeat(32)}/build-report.json`,
    document_manifest_hash: "b".repeat(64), index_fingerprint: "b".repeat(64), report_sha256: "b".repeat(64),
    configuration_snapshot_hash: "b".repeat(64), run_fingerprint: "b".repeat(64),
    report_status: "completed", selection_revision: 1,
  },
  legacy_report_reference: null, reason_code: "selection_verified", check_level: "report", store_checked: false,
};
const askAnswer = {
  query_id: "b".repeat(64), question: "What does the figure show?", outcome: "answered",
  answer: "The figure shows a steady rise [T1] [I1].", model_reported_insufficient_evidence: false,
  failure_code: null, model_identifier: "configured-model", index_fingerprint: "b".repeat(64),
  document_manifest_hash: "b".repeat(64), run_fingerprint: "b".repeat(64), context_fingerprint: "b".repeat(64),
  selected_build_id: "c".repeat(32), selected_report_sha256: "b".repeat(64), selection_revision: 1,
  text_evidence: [{ label: "T1", document_id: `doc_${"d".repeat(64)}`, source_path: "manual.pdf", page_number: 2,
    passage: "The figure rises from left to right.", record_id: "record-1", truncated: false }],
  image_evidence: [{ label: "I1", document_id: `doc_${"d".repeat(64)}`, source_path: "manual.pdf", page_number: 2,
    caption: "Chart evidence", record_id: "image-1", image_reference: "a".repeat(32), content_type: "image/png", size_bytes: 3 }],
  referenced_citations: ["T1", "I1"], warnings: [],
};

function jsonResponse(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function mount(initialPath = "/") {
  const router = createMemoryRouter([{ path: "*", element: <App /> }], { initialEntries: [initialPath] });
  render(<RouterProvider router={router} />);
  return router;
}

function defaultFetch() {
  return vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/health") return jsonResponse(health);
    if (path === "/api/v1/runtime/models") return jsonResponse(runtime);
    if (path === "/api/v1/chat/models") return jsonResponse(models);
    if (path === "/api/v1/index/selection") return jsonResponse(unselected);
    if (path === "/bootstrap-api/v1/bootstrap/status") return jsonResponse({
      mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2,
    });
    if (path.startsWith("/bootstrap-api/v1/bootstrap/documents")) return jsonResponse({
      inventory_status: "available", documents: [], manifest_hash: "a".repeat(64), total: 0,
      limit: 50, offset: 0, complete: true, max_upload_bytes: 16 * 1024 * 1024, max_pages: 200,
    });
    return jsonResponse({ error: { code: "not_found" } }, 404);
  });
}

describe("chat-first local application", () => {
  it("shows exactly four primary navigation sections without token entry", async () => {
    vi.stubGlobal("fetch", defaultFetch());
    mount();
    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(withinLinks(navigation)).toEqual(["Chat", "Documents", "Results", "Settings"]);
    expect(screen.getByRole("heading", { name: "Chat", level: 1 })).toBeInTheDocument();
    expect(screen.queryByLabelText("Bearer token")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Bootstrap bearer token")).not.toBeInTheDocument();
  });

  it("collapses and expands the sidebar while preserving accessible navigation names", async () => {
    vi.stubGlobal("fetch", defaultFetch());
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute("title", "Documents");
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute("aria-expanded", "true");
  });

  it("serves general chat with bounded in-tab conversation history and no bearer header", async () => {
    const fetcher = defaultFetch();
    fetcher.mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/health") return jsonResponse(health);
      if (path === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (path === "/api/v1/chat/models") return jsonResponse(models);
      if (path === "/api/v1/chat") {
        const body = JSON.parse(String(init?.body)) as { question: string; history: unknown[]; model?: string };
        return jsonResponse({ mode: "general", answer: `Reply to ${body.question}`, model: body.model ?? "gemma4:e2b" });
      }
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    mount();
    const input = screen.getByRole("textbox", { name: "Message" });
    await user.type(input, "First question");
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Reply to First question")).toBeInTheDocument();
    await user.type(input, "Follow-up");
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Reply to Follow-up")).toBeInTheDocument();
    const chatCalls = fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/chat");
    const second = JSON.parse(String(chatCalls[1]?.[1]?.body)) as { history: Array<{ user: string; assistant: string }> };
    expect(second.history).toEqual([{ user: "First question", assistant: "Reply to First question" }]);
    expect(fetcher.mock.calls.every(([, init]) => !new Headers(init?.headers).has("Authorization"))).toBe(true);
    expect(screen.getByText(/not document-grounded/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Reply to First question")).not.toBeInTheDocument();
    await user.type(input, "Fresh conversation");
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Reply to Fresh conversation")).toBeInTheDocument();
    const third = JSON.parse(String(fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/chat")[2]?.[1]?.body)) as { history: unknown[] };
    expect(third.history).toEqual([]);
  });

  it("switches to Document Chat and explains an unselected index with working navigation", async () => {
    vi.stubGlobal("fetch", defaultFetch());
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Chat with PDFs", pressed: false }));
    expect(await screen.findByText("Choose an index to chat with documents")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Prepare documents" })).toHaveAttribute("href", "/documents");
    expect(screen.getByRole("textbox", { name: "Message" })).toBeEnabled();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Draft before indexing");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("Draft before indexing");
    await user.click(screen.getByRole("link", { name: "Prepare documents" }));
    expect(await screen.findByRole("heading", { name: "Documents" })).toBeInTheDocument();
  });

  it("refreshes selection on window focus without discarding a drafted document question", async () => {
    let current: typeof unselected | typeof selected = unselected;
    const fallback = defaultFetch();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation((input, init) => String(input) === "/api/v1/index/selection" ? Promise.resolve(jsonResponse(current)) : fallback(input, init)));
    const user = userEvent.setup(); mount();
    await user.click(screen.getByRole("button", { name: "Chat with PDFs", pressed: false }));
    await screen.findByText("Choose an index to chat with documents");
    const composer = screen.getByRole("textbox", { name: "Message" });
    await user.type(composer, "Keep this draft");
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    current = selected;
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled());
    expect(composer).toHaveValue("Keep this draft");
  });

  it("preserves conversation, draft and model history through Settings and Documents, then restores on reload", async () => {
    const fallback = defaultFetch();
    const requests: Array<{ question: string; history: unknown[] }> = [];
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation((url, init) => {
      if (String(url) === "/api/v1/chat") { const body = JSON.parse(String(init?.body)); requests.push(body); return Promise.resolve(jsonResponse({ mode: "general", model: "gemma4:e2b", answer: `Answer ${body.question}` })); }
      return fallback(url, init);
    }));
    const user = userEvent.setup(); const router = mount();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Remember the observatory{Enter}");
    await screen.findByText("Answer Remember the observatory");
    await user.type(screen.getByRole("textbox", { name: "Message" }), "My unsent draft");
    await act(async () => { await router.navigate("/settings"); });
    await act(async () => { await router.navigate("/documents"); });
    await act(async () => { await router.navigate("/"); });
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("My unsent draft");
    expect(screen.getByText("Answer Remember the observatory")).toBeVisible();
    cleanup(); mount();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("My unsent draft");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("Answer My unsent draft");
    expect(requests[1]?.history).toEqual([{ user: "Remember the observatory", assistant: "Answer Remember the observatory" }]);
    const firstId = (screen.getByRole("combobox", { name: "Conversation" }) as HTMLSelectElement).value;
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByText("Answer My unsent draft")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Conversation" }), firstId);
    expect(screen.getByText("Answer My unsent draft")).toBeVisible();
  });

  it("honors mode query navigation on a reused Chat workspace and preserves separate drafts", async () => {
    vi.stubGlobal("fetch", defaultFetch()); const user = userEvent.setup(); const router = mount();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "General draft");
    await act(async () => { await router.navigate("/?mode=document"); });
    expect(screen.getByRole("button", { name: "Chat with PDFs", pressed: true })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("");
    await user.type(screen.getByRole("textbox", { name: "Message" }), "PDF draft");
    await act(async () => { await router.navigate("/?mode=general"); });
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("General draft");
    await act(async () => { await router.navigate("/?mode=document"); });
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveValue("PDF draft");
  });

  it("remembers the last selected conversation per mode and keeps URL, picker, reload and history aligned", async () => {
    vi.stubGlobal("fetch", defaultFetch()); const user = userEvent.setup(); const router = mount("/?mode=general");
    const picker = () => screen.getByRole("combobox", { name: "Conversation" }) as HTMLSelectElement;
    const input = () => screen.getByRole("textbox", { name: "Message" });
    await user.type(input(), "General A draft"); const a = picker().value;
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await user.type(input(), "General C draft"); const c = picker().value;
    await user.selectOptions(picker(), a);
    await user.click(screen.getByRole("button", { name: "Chat with PDFs", pressed: false }));
    await user.type(input(), "PDF B draft"); const b = picker().value;
    await user.click(screen.getByRole("button", { name: "General Chat", pressed: false }));
    expect(picker().value).toBe(a); expect(input()).toHaveValue("General A draft");
    expect(router.state.location.search).toContain("mode=general");
    const generalUrl = `${router.state.location.pathname}${router.state.location.search}`;
    cleanup(); const restored = mount(generalUrl);
    expect((screen.getByRole("combobox", { name: "Conversation" }) as HTMLSelectElement).value).toBe(a);
    await user.click(screen.getByRole("button", { name: "Chat with PDFs", pressed: false }));
    expect(picker().value).toBe(b); expect(input()).toHaveValue("PDF B draft");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "General Chat", pressed: false }));
      fireEvent.click(screen.getByRole("button", { name: "Chat with PDFs" }));
    });
    expect(picker().value).toBe(b); expect(input()).toHaveValue("PDF B draft");
    await user.selectOptions(picker(), c);
    expect(screen.getByRole("button", { name: "General Chat", pressed: true })).toBeInTheDocument();
    expect(input()).toHaveValue("General C draft");
    await act(async () => { await restored.navigate(-1); });
    expect(picker().value).toBe(b); expect(input()).toHaveValue("PDF B draft");
    await act(async () => { await restored.navigate(1); });
    expect(picker().value).toBe(c);
    await user.click(screen.getByRole("button", { name: "Chat with PDFs", pressed: false }));
    await act(async () => {
      fireEvent.change(picker(), { target: { value: c } });
      await restored.navigate(-1);
    });
    expect(picker().value).toBe(b);
    expect(input()).toHaveValue("PDF B draft");
    await user.selectOptions(picker(), c);
    await act(async () => { await restored.navigate("/settings"); await restored.navigate("/"); });
    expect(picker().value).toBe(c); expect(input()).toHaveValue("General C draft");
  });

  it("waits for model discovery before sending a saved model preference", async () => {
    window.localStorage.setItem("rag-pdf.general-chat-preferences", JSON.stringify({version:1, selectedModel:"qwen2.5:3b", temperature:0.4, maxOutputTokens:64}));
    let resolveModels!: (value: Response) => void;
    const pendingModels = new Promise<Response>(resolve => { resolveModels = resolve; });
    const fallback = defaultFetch();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url, init) => String(url) === "/api/v1/chat/models" ? pendingModels : String(url) === "/api/v1/chat" ? Promise.resolve(jsonResponse({mode:"general",model:"qwen2.5:3b",answer:"Saved model response"})) : fallback(url, init));
    vi.stubGlobal("fetch", fetcher); const user = userEvent.setup(); mount();
    await user.type(screen.getByRole("textbox", {name:"Message"}), "Use my saved model");
    expect(screen.getByRole("button", {name:"Send message"})).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(fetcher.mock.calls.some(([url]) => String(url) === "/api/v1/chat")).toBe(false);
    await act(async () => resolveModels(jsonResponse(models)));
    await user.click(screen.getByRole("button", {name:"Send message"}));
    await screen.findByText("Saved model response");
    const call = fetcher.mock.calls.find(([url]) => String(url) === "/api/v1/chat");
    expect(JSON.parse(String(call?.[1]?.body)).model).toBe("qwen2.5:3b");
  });

  it("keeps the active chat when history points to a deleted conversation", async () => {
    vi.stubGlobal("fetch", defaultFetch()); vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup(); const router = mount();
    await user.type(screen.getByRole("textbox", {name:"Message"}), "Preserve A");
    const a = (screen.getByRole("combobox", {name:"Conversation"}) as HTMLSelectElement).value;
    await user.click(screen.getByRole("button", {name:"New chat"}));
    const deletedUrl = `${router.state.location.pathname}${router.state.location.search}`;
    await user.click(screen.getByRole("button", {name:"Delete conversation"}));
    await act(async () => { await router.navigate(deletedUrl); });
    const picker = screen.getByRole("combobox", {name:"Conversation"}) as HTMLSelectElement;
    expect(picker.value).toBe(a); expect(picker.options).toHaveLength(1);
    expect(screen.getByRole("textbox", {name:"Message"})).toHaveValue("Preserve A");
    expect(router.state.location.search).toContain(a);
  });

  it("shows a truthful Ollama-unavailable error and does not fabricate an answer", async () => {
    const fetcher = defaultFetch();
    fetcher.mockImplementation(async (input) => {
      if (String(input) === "/health") return jsonResponse(health);
      if (String(input) === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (String(input) === "/api/v1/chat/models") return jsonResponse({ ...models, availability: "unavailable", configured_model_available: false, available_models: [], error_code: "ollama_unavailable" });
      if (String(input) === "/api/v1/chat") return jsonResponse({ error: { code: "ollama_unavailable" } }, 503);
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    mount();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Hello");
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Ollama is not responding. Start the configured local Ollama service and try again.")).toBeInTheDocument();
    expect(screen.queryByText(/Here is an answer/)).not.toBeInTheDocument();
  });

  it("prevents duplicate sends and keeps a pending response in its conversation", async () => {
    let resolveChat!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { resolveChat = resolve; });
    const fetcher = defaultFetch();
    fetcher.mockImplementation(async (input) => {
      if (String(input) === "/api/v1/chat") return pending;
      if (String(input) === "/health") return jsonResponse(health);
      if (String(input) === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (String(input) === "/api/v1/chat/models") return jsonResponse(models);
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    const router = mount();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Wait for this response");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/chat")).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Chat with PDFs", pressed: false })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "New chat" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Conversation" })).toBeDisabled();
    await act(async () => { await router.navigate("/settings"); });
    await act(async () => {
      resolveChat(jsonResponse({ mode: "general", answer: "Late response", model: "gemma4:e2b" }));
      await pending;
    });
    await act(async () => { await router.navigate("/"); });
    expect(screen.getByText("Wait for this response")).toBeInTheDocument();
    expect(screen.getByText("Late response")).toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/chat")).toHaveLength(1);
  });

  it("does not attach a late pre-reload response to a restored browser session", async () => {
    let resolveChat!: (response: Response) => void;
    const pending = new Promise<Response>(resolve => { resolveChat = resolve; });
    const fallback = defaultFetch();
    const fetcher = vi.fn<typeof fetch>().mockImplementation((url, init) => String(url) === "/api/v1/chat" ? pending : fallback(url, init));
    vi.stubGlobal("fetch", fetcher); const user = userEvent.setup(); mount();
    await user.type(screen.getByRole("textbox", {name:"Message"}), "Request before reload");
    await user.click(screen.getByRole("button", {name:"Send message"}));
    cleanup(); mount();
    expect(screen.getByText("Request before reload")).toBeVisible();
    expect(screen.getByText(/previous request may still be processing/)).toBeVisible();
    await act(async () => { resolveChat(jsonResponse({mode:"general",model:"gemma4:e2b",answer:"Old browser response"})); await pending; });
    expect(screen.queryByText("Old browser response")).not.toBeInTheDocument();
    expect(fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/chat")).toHaveLength(1);
  });

  it("uses selected-index Ask evidence and authenticated image references in Document Chat", async () => {
    window.localStorage.setItem("rag-pdf.general-chat-preferences", JSON.stringify({
      version: 1, selectedModel: "qwen2.5:3b", temperature: 1.2, maxOutputTokens: 128,
    }));
    const fetcher = defaultFetch();
    fetcher.mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/health") return jsonResponse(health);
      if (path === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (path === "/api/v1/chat/models") return jsonResponse(models);
      if (path === "/api/v1/index/selection") return jsonResponse(selected);
      if (path === "/api/v1/ask") return jsonResponse(askAnswer);
      if (path === "/api/v1/ask/images/" + "a".repeat(32)) return new Response(new Uint8Array([1, 2, 3]), { headers: { "Content-Type": "image/png" } });
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    const create = vi.fn(() => "blob:document-evidence");
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    vi.stubGlobal("fetch", fetcher);
    mount("/?mode=document");
    expect(await screen.findByText("PDF collection selected")).toBeInTheDocument();
    const input = await screen.findByRole("textbox", { name: "Message" });
    await user.type(input, "What does the figure show?");
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("button", { name: "[T1]" }));
    await user.click(await screen.findByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(askAnswer.answer);
    expect(await screen.findByText("The figure rises from left to right.")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "Chart evidence" })).toHaveAttribute("src", "blob:document-evidence");
    const imageCall = fetcher.mock.calls.find(([url]) => String(url).includes("/api/v1/ask/images/"));
    expect(imageCall).toBeDefined();
    expect(new Headers(imageCall?.[1]?.headers).has("Authorization")).toBe(false);
    const askCall = fetcher.mock.calls.find(([url]) => String(url) === "/api/v1/ask");
    expect(JSON.parse(String(askCall?.[1]?.body))).toEqual({ question: "What does the figure show?" });
    expect(fetcher.mock.calls.some(([url]) => String(url) === "/api/v1/chat")).toBe(false);
    await user.click(within(screen.getByRole("complementary", { name: "Document evidence" })).getByRole("button", { name: "Close evidence" }));
    expect(revoke).toHaveBeenCalledWith("blob:document-evidence");
  });

  it("saves installed model and generation preferences for the next General Chat request", async () => {
    const fetcher = defaultFetch();
    fetcher.mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/health") return jsonResponse(health);
      if (path === "/api/v1/runtime/models") return jsonResponse(runtime);
      if (path === "/api/v1/chat/models") return jsonResponse(models);
      if (path === "/api/v1/chat") {
        const body = JSON.parse(String(init?.body)) as { model?: string };
        return jsonResponse({ mode: "general", answer: "A local answer.", model: body.model });
      }
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    const router = mount("/settings");
    await user.selectOptions(await screen.findByRole("combobox", { name: "Use this installed model" }), "qwen2.5:3b");
    fireEvent.change(screen.getByRole("slider", { name: "Temperature" }), { target: { value: "0.4" } });
    const tokenLimit = screen.getByRole("spinbutton", { name: "Maximum answer length" });
    await user.clear(tokenLimit);
    await user.type(tokenLimit, "768");
    expect(screen.queryByLabelText(/Thinking/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/context window/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save preferences" }));
    expect(await screen.findByText("Preferences saved in this browser.")).toBeInTheDocument();
    await router.navigate("/");
    await user.type(await screen.findByRole("textbox", { name: "Message" }), "Hello");
    await user.keyboard("{Enter}");
    expect(await screen.findByText("A local answer.")).toBeInTheDocument();
    const call = fetcher.mock.calls.find(([url]) => String(url) === "/api/v1/chat");
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      model: "qwen2.5:3b",
      generation: { temperature: 0.4, max_output_tokens: 768 },
    });
    expect(window.localStorage.getItem("rag-pdf.general-chat-preferences")).toContain("qwen2.5:3b");
  });

  it("resets a previously saved model that is no longer installed and reports the fallback", async () => {
    window.localStorage.setItem("rag-pdf.general-chat-preferences", JSON.stringify({
      version: 1, selectedModel: "removed:latest", temperature: 0.9, maxOutputTokens: 900,
    }));
    vi.stubGlobal("fetch", defaultFetch());
    mount("/settings");
    expect(await screen.findByText(/Saved model removed:latest is no longer installed/)).toHaveTextContent(/Using configured default gemma4:e2b/);
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Use this installed model" })).toHaveValue("gemma4:e2b");
      expect(window.localStorage.getItem("rag-pdf.general-chat-preferences")).toContain('"selectedModel":null');
    });
  });

  it("shows the unavailable Ollama state separately from an uninstalled default model", async () => {
    const fetcher = defaultFetch();
    fetcher.mockImplementation(async (input) => {
      if (String(input) === "/api/v1/chat/models") return jsonResponse({
        ...models, availability: "unavailable", configured_model_available: false, available_models: [], error_code: "ollama_unavailable",
      });
      if (String(input) === "/health") return jsonResponse(health);
      return jsonResponse({ error: { code: "not_found" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    mount("/settings");
    expect(await screen.findByText(/Ollama is unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/configured default is/)).toHaveTextContent("gemma4:e2b");
    expect(screen.queryByRole("combobox", { name: "Use this installed model" })).not.toBeInTheDocument();
  });

  it("shows an invalid saved-record message and starts from safe defaults", async () => {
    window.localStorage.setItem("rag-pdf.general-chat-preferences", "{broken");
    vi.stubGlobal("fetch", defaultFetch());
    mount("/settings");
    expect(await screen.findByText(/Saved General Chat preferences were invalid/)).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Temperature" })).toHaveValue("0.7");
    expect(screen.getByRole("spinbutton", { name: "Maximum answer length" })).toHaveValue(512);
    expect(window.localStorage.getItem("rag-pdf.general-chat-preferences")).toBeNull();
  });

  it("announces invalid answer limits and blocks saving until corrected", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", defaultFetch());
    mount("/settings");
    const tokenLimit = await screen.findByRole("spinbutton", { name: "Maximum answer length" });
    await user.clear(tokenLimit);
    await user.type(tokenLimit, "31");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a whole number from 32 to 2048.");
    expect(screen.getByRole("button", { name: "Save preferences" })).toBeDisabled();
  });

  it("resets preferences from the keyboard and persists the General Chat defaults", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", defaultFetch());
    mount("/settings");
    const reset = await screen.findByRole("button", { name: "Reset to defaults" });
    reset.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("General Chat defaults restored and saved in this browser.")).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("rag-pdf.general-chat-preferences") ?? "null")).toEqual({
      version: 1, selectedModel: null, temperature: 0.7, maxOutputTokens: 512,
    });
  });

  it("closes the mobile navigation with Escape and restores focus", async () => {
    vi.stubGlobal("fetch", defaultFetch());
    const user = userEvent.setup();
    mount();
    const menu = screen.getByRole("button", { name: "Open navigation" });
    await user.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(menu).toHaveFocus());
    expect(menu).toHaveAttribute("aria-expanded", "false");
  });
});

function withinLinks(element: HTMLElement): string[] {
  return Array.from(element.querySelectorAll("a")).map((link) => link.textContent?.trim() ?? "");
}
