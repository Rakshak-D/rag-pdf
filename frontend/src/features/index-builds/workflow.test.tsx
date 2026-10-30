import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
