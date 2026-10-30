import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../../App";
import { REQUIRED_RESOLVED_CONFIG_FIELDS } from "./configValidation";

const hash = "a".repeat(64);
const fingerprint = "b".repeat(64);
const health = { status: "ok", api_version: "v1" };
const service = { mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 };
const preview = { document_count: 2, manifest_hash: hash, index_fingerprint: fingerprint, run_fingerprint: "c".repeat(64), existing_index: false, fresh_build_eligible: true, reason_code: "fresh_build_available" };
const jobBase = {
  job_id: "9de7bd41-7118-4cff-8eca-2d82bf8f7029", client_request_id: "client-request-000001", status: "queued",
  manifest_hash: hash, index_fingerprint: fingerprint, created_at: "2026-10-30T00:00:00+00:00", started_at: null,
  finished_at: null, error_code: null, build_id: null, report_reference: null, text_records: null, image_records: null, partial_persistence: null,
};
const inventoryBuildId = "d".repeat(32);
const inventoryMatch = {
  candidate_id: inventoryBuildId, build_id: inventoryBuildId,
  report_reference: `build-artifacts/${inventoryBuildId}/build-report.json`,
  compatibility: "report_match", reason_code: "report_matches",
  expected_index_fingerprint: fingerprint, document_manifest_hash: hash,
  reported_index_fingerprint: fingerprint, reported_document_manifest_hash: hash,
  report_status: "completed", reported_text_records: 12, reported_image_records: 0,
  check_level: "report", store_checked: false,
};
function inventoryResponse(overrides: Record<string, unknown> = {}) {
  return {
    expected_index_fingerprint: fingerprint, document_manifest_hash: hash,
    candidates: [inventoryMatch], total_candidates: 1, offset: 0, limit: 25,
    complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false,
    ...overrides,
  };
}

function completeConfig(): string {
  const value: Record<string, unknown> = Object.fromEntries(REQUIRED_RESOLVED_CONFIG_FIELDS.map((key) => [key, null]));
  value.pdf_dir = "trusted-source";
  value.reset_collections_on_start = false;
  value.workbench_policy = { allow_destructive_index_reset: false, allow_collection_delete: false };
  return JSON.stringify(value, null, 2);
}

function json(value: unknown, status = 200): Response { return Response.json(value, { status }); }
function mount(path = "/index-builds") {
  const router = createMemoryRouter([{ path: "*", element: <App /> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}
function responseFor(path: string, init?: RequestInit): Response {
  if (path === "/health") return json(health);
  if (path === "/bootstrap-api/v1/bootstrap/status") return json(service);
  if (path === "/bootstrap-api/v1/bootstrap/preview") return json(preview);
  if (path === "/bootstrap-api/v1/bootstrap/index-inventory") return json(inventoryResponse());
  if (path === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST") {
    const request = JSON.parse(String(init.body)) as { client_request_id: string };
    return json({ job: { ...jobBase, client_request_id: request.client_request_id } }, 202);
  }
  if (path.startsWith("/bootstrap-api/v1/bootstrap/jobs?")) {
    const query = new URLSearchParams(path.split("?")[1]);
    return json({ jobs: [], limit: Number(query.get("limit")), offset: Number(query.get("offset")) });
  }
  if (path.startsWith("/bootstrap-api/v1/bootstrap/jobs/")) return json({ job: jobBase });
  return json({ error: { code: "not_found" } }, 404);
}

async function connectBootstrap(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Bootstrap bearer token"), "bootstrap-only-secret");
  await user.click(screen.getByRole("button", { name: "Connect to bootstrap" }));
  await screen.findByText("Bootstrap connected", { exact: true });
}

async function preparePreview(user: ReturnType<typeof userEvent.setup>) {
  fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig() } });
  await user.click(screen.getByRole("button", { name: "Preview index build" }));
  await screen.findByText("Server-computed build identity");
}

describe("bootstrap index-build workflow", () => {
  it("uses a separate bootstrap connection and rejects a runtime-shaped response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/status") return json({ components: [] });
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount();
    expect(screen.queryByLabelText("Bearer token")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Bootstrap bearer token"), "wrong-service-token");
    await user.click(screen.getByRole("button", { name: "Connect to bootstrap" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected response");
    expect(screen.queryByText(/Authenticated bootstrap mode/)).not.toBeInTheDocument();
    expect(fetcher.mock.calls.some(([, init]) => new Headers(init?.headers).get("Authorization") === "Bearer wrong-service-token")).toBe(true);
    expect(fetcher.mock.calls.every(([url]) => !String(url).includes("wrong-service-token"))).toBe(true);
  });

  it("previews server identities, requires explicit acknowledgment, submits once, and opens the real job detail", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => responseFor(String(input), init));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user);
    expect(fetcher.mock.calls.some(([url]) => String(url) === "/bootstrap-api/v1/bootstrap/status")).toBe(true);
    await preparePreview(user);
    expect(screen.getAllByText(hash).length).toBeGreaterThan(0);
    expect(screen.getAllByText(fingerprint).length).toBeGreaterThan(0);
    const start = screen.getByRole("button", { name: "Start fresh build" });
    expect(start).toBeDisabled();
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST")).toHaveLength(0);
    expect(fetcher.mock.calls.filter(([url]) => String(url) === "/bootstrap-api/v1/bootstrap/index-inventory")).toHaveLength(0);
    await user.click(screen.getByRole("checkbox", { name: new RegExp(hash) }));
    expect(start).toBeEnabled();
    await user.click(start);
    expect(await screen.findByRole("heading", { name: "Build job" })).toBeInTheDocument();
    await screen.findByText("Queued", { exact: true });
    const posts = fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST");
    expect(posts).toHaveLength(1);
    const body = JSON.parse(String(posts[0]?.[1]?.body));
    expect(body.expected_manifest_hash).toBe(hash);
    expect(body.expected_index_fingerprint).toBe(fingerprint);
    expect(body.client_request_id).toMatch(/^[A-Za-z0-9_-]{16,80}$/);
    expect(new Headers(posts[0]?.[1]?.headers).get("Authorization")).toBe("Bearer bootstrap-only-secret");
  });

  it("invalidates an in-flight preview after a configuration edit", async () => {
    let resolvePreview!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/preview") return new Promise<Response>((resolve) => { resolvePreview = resolve; });
      return Promise.resolve(responseFor(String(input), init));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user);
    fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig() } });
    await user.click(screen.getByRole("button", { name: "Preview index build" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig().replace('"text_k": null', '"text_k": 17') } });
    resolvePreview(json(preview));
    await waitFor(() => expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument());
    expect(screen.queryByText(hash)).not.toBeInTheDocument();
  });

  it("rejects incomplete, duplicate-key, and destructive drafts before preview", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => responseFor(String(input), init));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user);
    const editor = screen.getByRole("textbox", { name: /Resolved configuration JSON/ });
    fireEvent.change(editor, { target: { value: "{}" } });
    expect(await screen.findByText(/Missing required fields/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview index build" })).toBeDisabled();
    const duplicated = completeConfig().replace('"text_k": null,', '"text_k": null,\n  "text_k": 10,');
    fireEvent.change(editor, { target: { value: duplicated } });
    expect(await screen.findByText(/Duplicate JSON object keys/)).toBeInTheDocument();
    const destructive = JSON.parse(completeConfig()) as Record<string, unknown>;
    destructive.reset_collections_on_start = true;
    fireEvent.change(editor, { target: { value: JSON.stringify(destructive) } });
    expect(await screen.findByText(/Fresh bootstrap builds require reset/)).toBeInTheDocument();
    expect(fetcher.mock.calls.some(([url]) => String(url) === "/bootstrap-api/v1/bootstrap/preview")).toBe(false);
  });

  it("blocks an existing fingerprint-owned directory", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/preview") return json({ ...preview, existing_index: true, fresh_build_eligible: false, reason_code: "index_already_exists" });
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    expect(screen.getByText("Existing index blocks fresh build")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: new RegExp(hash) })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start fresh build" })).toBeDisabled();
  });

  it("inspects reports even when an existing index blocks fresh builds", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/preview") return json({ ...preview, existing_index: true, fresh_build_eligible: false, reason_code: "index_already_exists" });
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    expect(screen.getByRole("button", { name: "Start fresh build" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText("Report compatibility only.")).toBeInTheDocument();
    expect(screen.getByText(inventoryBuildId)).toBeInTheDocument();
    expect(screen.getByText("Validated text records").parentElement).toHaveTextContent("12");
    expect(screen.getByText(/Chroma collections were not checked/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /activate|repair|delete|retry/i })).not.toBeInTheDocument();
    const inventoryPost = fetcher.mock.calls.find(([url]) => String(url) === "/bootstrap-api/v1/bootstrap/index-inventory");
    expect(inventoryPost?.[1]?.method).toBe("POST");
    expect(new Headers(inventoryPost?.[1]?.headers).get("Authorization")).toBe("Bearer bootstrap-only-secret");
    expect(JSON.parse(String(inventoryPost?.[1]?.body))).toEqual({ resolved_config: JSON.parse(completeConfig()), expected_manifest_hash: hash, limit: 25, offset: 0 });
    expect(fetcher.mock.calls.some(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST")).toBe(false);
  });

  it("renders each report classification without treating it as index readiness", async () => {
    const candidates = [
      inventoryMatch,
      { ...inventoryMatch, candidate_id: "e".repeat(32), build_id: "e".repeat(32), report_reference: `build-artifacts/${"e".repeat(32)}/build-report.json`, compatibility: "mismatch", reason_code: "index_fingerprint_mismatch", reported_index_fingerprint: "c".repeat(64), reported_text_records: null, reported_image_records: null },
      { ...inventoryMatch, candidate_id: "f".repeat(32), build_id: "f".repeat(32), report_reference: `build-artifacts/${"f".repeat(32)}/build-report.json`, compatibility: "failed", reason_code: "build_failed", report_status: "failed", reported_text_records: null, reported_image_records: null },
      { ...inventoryMatch, candidate_id: "1".repeat(32), build_id: "1".repeat(32), report_reference: `build-artifacts/${"1".repeat(32)}/build-report.json`, compatibility: "incomplete", reason_code: "partial_persistence", reported_text_records: null, reported_image_records: null },
      { ...inventoryMatch, candidate_id: "entry-0123456789abcdef", build_id: null, report_reference: null, compatibility: "invalid", reason_code: "build_id_invalid", reported_index_fingerprint: null, reported_document_manifest_hash: null, report_status: null, reported_text_records: null, reported_image_records: null },
      { ...inventoryMatch, candidate_id: "entry-fedcba9876543210", build_id: null, report_reference: null, compatibility: "unavailable", reason_code: "report_unavailable", reported_index_fingerprint: null, reported_document_manifest_hash: null, report_status: null, reported_text_records: null, reported_image_records: null },
    ];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") return json(inventoryResponse({ candidates, total_candidates: candidates.length }));
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    for (const label of ["Report matches current plan", "Report identity differs", "Build report records failure", "Build evidence is incomplete", "Report evidence is invalid", "Report evidence is unavailable"]) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText(/Report compatibility only/)).toBeInTheDocument();
  });

  it("distinguishes complete empty inventory from a bounded incomplete scan", async () => {
    let response = inventoryResponse({ candidates: [], total_candidates: 0, reason_code: "no_build_reports" });
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") return json(response);
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText("No build reports were found in the trusted inventory.")).toBeInTheDocument();
    response = inventoryResponse({ candidates: [], total_candidates: null, complete: false, reason_code: "inventory_limit_exceeded" });
    await user.click(screen.getByRole("button", { name: "Refresh inventory" }));
    expect(await screen.findByText("Inventory could not be fully inspected.")).toBeInTheDocument();
    expect(screen.getByText(/bounded inspection limit was reached/i)).toBeInTheDocument();
    expect(screen.queryByText("No build reports were found in the trusted inventory.")).not.toBeInTheDocument();
  });

  it("pages lexically, supports refresh, and stays within the backend offset limit", async () => {
    const makeCandidates = (offset: number, count: number) => Array.from({ length: count }, (_, index) => {
      const number = offset + index + 1;
      const id = number.toString(16).padStart(32, "0");
      return { ...inventoryMatch, candidate_id: id, build_id: id, report_reference: `build-artifacts/${id}/build-report.json` };
    });
    const requests: Array<{ offset: number; limit: number }> = [];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") {
        const body = JSON.parse(String(init?.body)) as { offset: number; limit: number };
        requests.push(body);
        return json(inventoryResponse({ candidates: makeCandidates(body.offset, body.offset === 0 ? 25 : 5), total_candidates: 30, offset: body.offset, limit: body.limit }));
      }
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText("Candidates 1–25 of 30")).toBeInTheDocument();
    await user.click(within(screen.getByRole("group", { name: "Inventory pagination" })).getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Candidates 26–30 of 30")).toBeInTheDocument();
    await user.click(within(screen.getByRole("group", { name: "Inventory pagination" })).getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Candidates 1–25 of 30")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh inventory" }));
    await waitFor(() => expect(requests).toHaveLength(4));
    expect(requests.map(({ offset }) => offset)).toEqual([0, 25, 0, 0]);
    expect(requests.every(({ offset, limit }) => offset >= 0 && offset <= 512 && limit === 25)).toBe(true);
  });

  it("clears inventory when configuration changes and ignores a stale scan response", async () => {
    let resolveInventory!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") return new Promise<Response>((resolve) => { resolveInventory = resolve; });
      return Promise.resolve(responseFor(String(input), init));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig().replace('"text_k": null', '"text_k": 19') } });
    resolveInventory(json(inventoryResponse()));
    await waitFor(() => expect(screen.queryByText("Build report inventory")).not.toBeInTheDocument());
    expect(screen.queryByText(inventoryBuildId)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Inspect build reports/ })).not.toBeInTheDocument();
  });

  it("invalidates inventory on a replacement preview and clears it on disconnect", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => responseFor(String(input), init));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText(inventoryBuildId)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Preview index build" }));
    await screen.findByText("Server-computed build identity");
    expect(screen.queryByText(inventoryBuildId)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText(inventoryBuildId)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Disconnect bootstrap" }));
    await waitFor(() => expect(screen.queryByText("Build report inventory")).not.toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: /Resolved configuration JSON/ })).toHaveValue("");
  });

  it("disables the next inventory page when the maximum supported offset would be exceeded", async () => {
    const makeCandidates = (offset: number, count: number) => Array.from({ length: count }, (_, index) => {
      const id = (offset + index + 1).toString(16).padStart(32, "0");
      return { ...inventoryMatch, candidate_id: id, build_id: id, report_reference: `build-artifacts/${id}/build-report.json` };
    });
    const offsets: number[] = [];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") {
        const body = JSON.parse(String(init?.body)) as { offset: number; limit: number };
        offsets.push(body.offset);
        return json(inventoryResponse({ candidates: makeCandidates(body.offset, Math.min(25, 512 - body.offset)), total_candidates: 512, offset: body.offset, limit: body.limit }));
      }
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    for (let page = 0; page < 20; page += 1) {
      await user.click(within(screen.getByRole("group", { name: "Inventory pagination" })).getByRole("button", { name: "Next" }));
    }
    expect(offsets.at(-1)).toBe(500);
    expect(within(screen.getByRole("group", { name: "Inventory pagination" })).getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("invalidates preview and inventory when the source manifest changed during inspection", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") return json({ error: { code: "manifest_changed" } }, 409);
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText(/PDF source changed during inspection/)).toBeInTheDocument();
    expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument();
    expect(screen.queryByText("Build report inventory")).not.toBeInTheDocument();
  });

  it("rejects inventory identities that disagree with the active preview", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/index-inventory") return json(inventoryResponse({ document_manifest_hash: "e".repeat(64) }));
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("button", { name: "Inspect build reports" }));
    expect(await screen.findByText(/identity did not match the active preview/i)).toBeInTheDocument();
    expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument();
    expect(screen.queryByText(inventoryBuildId)).not.toBeInTheDocument();
  });

  it("invalidates preview confirmation after the server rejects changed identity", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST") return json({ error: { code: "manifest_changed" } }, 409);
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("checkbox", { name: new RegExp(hash) }));
    await user.click(screen.getByRole("button", { name: "Start fresh build" }));
    expect(await screen.findByText("The source or configuration identity changed since preview. Preview again and confirm the new identities.")).toBeInTheDocument();
    expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start fresh build" })).not.toBeInTheDocument();
  });

  it("prevents a second click while one POST is still pending", async () => {
    let resolveSubmission!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST") return new Promise<Response>((resolve) => { resolveSubmission = resolve; });
      return Promise.resolve(responseFor(String(input), init));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("checkbox", { name: new RegExp(hash) }));
    const start = screen.getByRole("button", { name: "Start fresh build" });
    fireEvent.click(start); fireEvent.click(start);
    const posts = fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST");
    expect(posts).toHaveLength(1);
    const payload = JSON.parse(String(posts[0]?.[1]?.body)) as { client_request_id: string };
    resolveSubmission(json({ job: { ...jobBase, client_request_id: payload.client_request_id } }, 202));
    await screen.findByRole("heading", { name: "Build job" });
  });

  it("keeps uncertain submission read-only until history is checked and a new intent is explicit", async () => {
    let postCount = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST") { postCount += 1; throw new TypeError("connection lost"); }
      return responseFor(String(input), init);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user); await preparePreview(user);
    await user.click(screen.getByRole("checkbox", { name: new RegExp(hash) }));
    await user.click(screen.getByRole("button", { name: "Start fresh build" }));
    expect(await screen.findByRole("heading", { name: "The server may have accepted this request" })).toBeInTheDocument();
    expect(postCount).toBe(1);
    await user.click(screen.getByRole("button", { name: "Find request ID in job history" }));
    expect(await screen.findByText(/No build jobs are recorded/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start fresh build" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I reviewed the checked history/ }));
    await user.click(screen.getByRole("button", { name: "Allow a new explicit build request" }));
    expect(screen.getByRole("button", { name: "Start fresh build" })).toBeEnabled();
    expect(postCount).toBe(1);
  });

  it("renders succeeded, failed, and interrupted jobs without promising retrieval success", async () => {
    for (const state of ["succeeded", "failed", "interrupted"] as const) {
      cleanup();
      const record = state === "succeeded" ? { ...jobBase, status: state, finished_at: "2026-10-30T01:00:00+00:00", build_id: "d".repeat(32), report_reference: `build-artifacts/${"d".repeat(32)}/build-report.json`, text_records: 12, image_records: 0, partial_persistence: false }
        : { ...jobBase, status: state, finished_at: "2026-10-30T01:00:00+00:00", error_code: state === "failed" ? "build_failed" : "server_restarted", partial_persistence: state === "failed" ? true : null };
      const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
        if (String(input).startsWith("/bootstrap-api/v1/bootstrap/jobs/")) return json({ job: record });
        return responseFor(String(input), init);
      });
      vi.stubGlobal("fetch", fetcher);
      const user = userEvent.setup(); mount(`/index-builds/${jobBase.job_id}`); await connectBootstrap(user);
      const label = ({ succeeded: "Succeeded", failed: "Failed", interrupted: "Interrupted" } as const)[state];
      expect(await screen.findByText(label, { exact: true })).toBeInTheDocument();
      if (state === "succeeded") {
        expect(screen.getByText("Manual runtime activation")).toBeInTheDocument();
        expect(screen.getByText(/does not guarantee retrieval or model execution/)).toBeInTheDocument();
      }
      if (state === "failed") expect(screen.getByText(/partial index data is preserved/i)).toBeInTheDocument();
    }
  });

  it("clears the bootstrap token and configuration draft on disconnect", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => responseFor(String(input), init));
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user);
    fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig() } });
    await user.click(screen.getByRole("button", { name: "Disconnect bootstrap" }));
    expect(screen.getByRole("textbox", { name: /Resolved configuration JSON/ })).toHaveValue("");
    expect(screen.getByLabelText("Bootstrap bearer token")).toHaveValue("");
    expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument();
  });

  it("does not restore an in-flight preview after disconnect", async () => {
    let resolvePreview!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/preview") return new Promise<Response>((resolve) => { resolvePreview = resolve; });
      return Promise.resolve(responseFor(String(input), init));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount(); await connectBootstrap(user);
    fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig() } });
    await user.click(screen.getByRole("button", { name: "Preview index build" }));
    await user.click(screen.getByRole("button", { name: "Disconnect bootstrap" }));
    resolvePreview(json(preview));
    expect(screen.getByRole("textbox", { name: /Resolved configuration JSON/ })).toHaveValue("");
    expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument();
  });

  it("ignores a preview response after navigation unmounts the workspace", async () => {
    let resolvePreview!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation((input, init) => {
      if (String(input) === "/bootstrap-api/v1/bootstrap/preview") return new Promise<Response>((resolve) => { resolvePreview = resolve; });
      return Promise.resolve(responseFor(String(input), init));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); const router = mount(); await connectBootstrap(user);
    fireEvent.change(screen.getByRole("textbox", { name: /Resolved configuration JSON/ }), { target: { value: completeConfig() } });
    await user.click(screen.getByRole("button", { name: "Preview index build" }));
    await user.click(screen.getByRole("link", { name: "Jobs" }));
    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
    resolvePreview(json(preview));
    expect(screen.queryByText("Server-computed build identity")).not.toBeInTheDocument();
    await router.navigate("/index-builds");
    expect(await screen.findByRole("heading", { name: "Provide a complete resolved configuration" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Resolved configuration JSON/ })).toHaveValue("");
  });
});
