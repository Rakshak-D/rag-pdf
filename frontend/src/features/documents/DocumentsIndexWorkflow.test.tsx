import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { REQUIRED_RESOLVED_CONFIG_FIELDS } from "../index-builds/configValidation";
import { DocumentsIndexWorkflow } from "./DocumentsIndexWorkflow";

const manifest = "a".repeat(64);
const fingerprint = "b".repeat(64);
const runFingerprint = "c".repeat(64);
const jobId = "9de7bd41-7118-4cff-8eca-2d82bf8f7029";

function config(): Record<string, unknown> {
  const value: Record<string, unknown> = Object.fromEntries(REQUIRED_RESOLVED_CONFIG_FIELDS.map((field) => [field, null]));
  value.pdf_dir = "trusted-source";
  value.reset_collections_on_start = false;
  value.workbench_policy = { allow_destructive_index_reset: false, allow_collection_delete: false };
  return value;
}

const preview = {
  document_count: 2, manifest_hash: manifest, index_fingerprint: fingerprint, run_fingerprint: runFingerprint,
  existing_index: false, fresh_build_eligible: true, reason_code: "fresh_build_available",
};
const queuedJob = {
  job_id: jobId, client_request_id: "build-request-00000001", status: "queued", manifest_hash: manifest,
  index_fingerprint: fingerprint, created_at: "2026-11-07T00:00:00+00:00", started_at: null, finished_at: null,
  error_code: null, build_id: null, report_reference: null, text_records: null, image_records: null, partial_persistence: null,
};

function json(value: unknown, status = 200): Response { return Response.json(value, { status }); }
function mount(sourceManifestHash: string | null = manifest, sourceCount: number | null = 2) {
  return render(<MemoryRouter><DocumentsIndexWorkflow
    token="bootstrap-token-for-workflow"
    status={{ mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 }}
    sourceManifestHash={sourceManifestHash} sourceCount={sourceCount} onRefreshDocuments={vi.fn()}
  /></MemoryRouter>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("DocumentsIndexWorkflow", () => {
  it("previews actual source identity, scans existing reports, and requires explicit confirmation before one build", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/configuration") return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.startsWith("/bootstrap-api/v1/bootstrap/jobs?")) return json({ jobs: [], limit: 25, offset: 0 });
      if (path === "/bootstrap-api/v1/bootstrap/preview") return json(preview);
      if (path === "/bootstrap-api/v1/bootstrap/index-inventory") return json({
        expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0,
        offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false,
      });
      if (path === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST") {
        const submitted = JSON.parse(String(init.body)) as { client_request_id: string };
        return json({ job: { ...queuedJob, client_request_id: submitted.client_request_id } }, 202);
      }
      if (path === "/bootstrap-api/v1/bootstrap/index-inventory") return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    mount();

    expect(await screen.findByText("Using the trusted server configuration")).toBeInTheDocument();
    expect(await screen.findByText("A fresh build can be submitted")).toBeInTheDocument();
    expect(screen.getByText("2", { selector: "dd" })).toBeInTheDocument();
    await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url) === "/bootstrap-api/v1/bootstrap/index-inventory")).toBe(true));
    expect(await screen.findByText("Report check complete.")).toBeInTheDocument();
    const buildButton = screen.getByRole("button", { name: "Prepare documents" });
    expect(buildButton).toBeDisabled();
    await user.click(screen.getByLabelText(/Prepare all 2 PDFs/));
    await user.click(buildButton);
    expect(await screen.findByLabelText("Build status queued")).toBeInTheDocument();
    expect(screen.queryByText("Build completed and the server validated its report. It is not selected automatically.")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\b\d+%\b/);

    const post = fetcher.mock.calls.find(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST");
    expect(post).toBeDefined();
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      resolved_config: config(), expected_manifest_hash: manifest, expected_index_fingerprint: fingerprint,
    });
    expect(JSON.parse(String(post?.[1]?.body)).client_request_id).toMatch(/^[a-f0-9]{32}$/);
    expect(fetcher.mock.calls.filter(([url, init]) => String(url) === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST")).toHaveLength(1);
  });

  it("does not retry an uncertain job submission and requires readback plus explicit intent", async () => {
    let postCount = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/configuration") return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.startsWith("/bootstrap-api/v1/bootstrap/jobs?")) {
        const limit = Number(new URL(`http://local${path}`).searchParams.get("limit"));
        return json({ jobs: [], limit, offset: 0 });
      }
      if (path === "/bootstrap-api/v1/bootstrap/preview") return json(preview);
      if (path === "/bootstrap-api/v1/bootstrap/jobs" && init?.method === "POST") { postCount += 1; throw new TypeError("connection interrupted"); }
      if (path === "/bootstrap-api/v1/bootstrap/index-inventory") return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); mount();
    await screen.findByText("Using the trusted server configuration");
    await screen.findByText("A fresh build can be submitted");
    await user.click(screen.getByLabelText(/Prepare all 2 PDFs/));
    await user.click(screen.getByRole("button", { name: "Prepare documents" }));
    expect(await screen.findByRole("group", { name: "Build submission needs confirmation" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check whether the build was accepted" }));
    expect(await screen.findByText(/explicitly intend to submit a new build/)).toBeInTheDocument();
    expect(postCount).toBe(1);
    expect(screen.queryByRole("button", { name: "Prepare documents" })).not.toBeInTheDocument();
  });

  it("does not preview an empty source and clears a preview when the manifest changes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/bootstrap-api/v1/bootstrap/configuration") return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.startsWith("/bootstrap-api/v1/bootstrap/jobs?")) return json({ jobs: [], limit: 25, offset: 0 });
      if (path === "/bootstrap-api/v1/bootstrap/preview") return json(preview);
      if (path === "/bootstrap-api/v1/bootstrap/index-inventory") return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const view = mount(null, 0);
    await screen.findByText("Using the trusted server configuration");
    expect(screen.getByRole("button", { name: "Choose a PDF" })).toBeEnabled();
    expect(fetcher.mock.calls.some(([url]) => String(url) === "/bootstrap-api/v1/bootstrap/preview")).toBe(false);
    view.rerender(<MemoryRouter><DocumentsIndexWorkflow
      token="bootstrap-token-for-workflow"
      status={{ mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 }}
      sourceManifestHash={manifest} sourceCount={2} onRefreshDocuments={vi.fn()}
    /></MemoryRouter>);
    await screen.findByText("A fresh build can be submitted");
    view.rerender(<MemoryRouter><DocumentsIndexWorkflow
      token="bootstrap-token-for-workflow"
      status={{ mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 }}
      sourceManifestHash={"d".repeat(64)} sourceCount={2} onRefreshDocuments={vi.fn()}
    /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText("A fresh build can be submitted")).not.toBeInTheDocument());
  });
  it("resumes a running job after navigation and automatically checks completed reports", async () => {
    let previewCalls = 0;
    let inventoryCalls = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = String(input);
      if (path.endsWith("/configuration")) return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.includes("/jobs?")) return json({ jobs: [{ ...queuedJob, status: "running" }], limit: 25, offset: 0 });
      if (path.endsWith(`/jobs/${jobId}`)) return json({ job: { ...queuedJob, status: "succeeded", build_id: "d".repeat(32), report_reference: `build-artifacts/${"d".repeat(32)}/build-report.json`, text_records: 2, image_records: 0, partial_persistence: false } });
      if (path.endsWith("/preview")) { previewCalls++; return json({ ...preview, existing_index: true, fresh_build_eligible: false, reason_code: "index_already_exists" }); }
      if (path.endsWith("/index-inventory")) { inventoryCalls++; return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false }); }
      if (path === "/bootstrap-api/v1/bootstrap/index-inventory") return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    mount();
    expect(await screen.findByLabelText("Build status running")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Build status succeeded")).toBeInTheDocument(), { timeout: 4000 });
    await waitFor(() => expect(inventoryCalls).toBe(2));
    expect(previewCalls).toBe(2);
    expect(screen.getByRole("heading", { name: "Preparation needs review" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare documents" })).not.toBeInTheDocument();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST" && String(init.body).includes("client_request_id"))).toBe(false);
  });

  it("finds a compatible report beyond the first inventory page without submitting a build", async () => {
    const offsets: number[] = [];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/configuration")) return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.includes("/jobs?")) return json({ jobs: [], limit: 25, offset: 0 });
      if (path.endsWith("/preview")) return json({ ...preview, existing_index: true, fresh_build_eligible: false, reason_code: "index_already_exists" });
      if (path.endsWith("/index-inventory")) {
        const offset = JSON.parse(String(init?.body)).offset as number; offsets.push(offset);
        return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest,
          candidates: [{ candidate_id: "d".repeat(32), build_id: "d".repeat(32), report_reference: `build-artifacts/${"d".repeat(32)}/build-report.json`, compatibility: "report_match", reason_code: "report_matches", expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, reported_index_fingerprint: fingerprint, reported_document_manifest_hash: manifest, report_status: "completed", reported_text_records: 3, reported_image_records: 0, check_level: "report", store_checked: false }].flatMap<unknown>(candidate => offset === 0 ? Array.from({ length: 25 }, (_, n) => ({ ...candidate, candidate_id: (n + 1).toString(16).padStart(32, "0"), build_id: (n + 1).toString(16).padStart(32, "0"), report_reference: `build-artifacts/${(n + 1).toString(16).padStart(32, "0")}/build-report.json`, compatibility: "mismatch", reason_code: "identity_mismatch", reported_text_records: null, reported_image_records: null })) : [candidate]),
          total_candidates: 26, offset, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      }
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher); mount();
    expect(await screen.findByRole("heading", { name: "Use your prepared collection" })).toBeInTheDocument();
    expect(offsets).toEqual([0, 25]);
    expect(fetcher.mock.calls.some(([url, init]) => String(url).endsWith("/jobs") && init?.method === "POST")).toBe(false);
  });

  it("recovers an accepted uncertain submission without permanently locking preparation", async () => {
    let request = "";
    let posts = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/configuration")) return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.includes("/jobs?")) {
        const limit = Number(new URL(`http://local${path}`).searchParams.get("limit"));
        return json({ jobs: limit === 100 ? [{ ...queuedJob, client_request_id: request }] : [], limit, offset: 0 });
      }
      if (path.endsWith("/preview")) return json(preview);
      if (path.endsWith("/index-inventory")) return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      if (path.endsWith("/jobs") && init?.method === "POST") { posts++; request = JSON.parse(String(init.body)).client_request_id; throw new TypeError("interrupted"); }
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher); const user = userEvent.setup(); mount();
    await screen.findByRole("button", { name: "Prepare documents" });
    await user.click(screen.getByLabelText(/Prepare all 2 PDFs/));
    await user.click(screen.getByRole("button", { name: "Prepare documents" }));
    await user.click(await screen.findByRole("button", { name: "Check whether the build was accepted" }));
    expect(await screen.findByRole("heading", { name: "Preparing your documents" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Build submission needs confirmation" })).not.toBeInTheDocument();
    expect(posts).toBe(1);
  });

  it.each(["failed", "interrupted"] as const)("shows safe recovery for a %s preparation", async status => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async input => {
      const path = String(input);
      if (path.endsWith("/configuration")) return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.includes("/jobs?")) return json({ jobs: [{ ...queuedJob, status, error_code: "worker_failed" }], limit: 25, offset: 0 });
      if (path.endsWith("/preview")) return json({ ...preview, existing_index: true, fresh_build_eligible: false, reason_code: "index_already_exists" });
      if (path.endsWith("/index-inventory")) return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: 0, offset: 0, limit: 25, complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false });
      return json({ error: { code: "unexpected_route" } }, 404);
    });
    vi.stubGlobal("fetch", fetcher); mount();
    expect(await screen.findByRole("heading", { name: "Preparation stopped" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review preparation" })).toHaveAttribute("href", "/index-builds");
    expect(screen.queryByRole("button", { name: "Prepare documents" })).not.toBeInTheDocument();
    expect(screen.getByLabelText(`Build status ${status}`)).toBeInTheDocument();
  });

  it("does not offer a fresh build when the completed-report scan is incomplete", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async input => {
      const path = String(input);
      if (path.endsWith("/configuration")) return json({ schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: config() });
      if (path.includes("/jobs?")) return json({ jobs: [], limit: 25, offset: 0 });
      if (path.endsWith("/preview")) return json(preview);
      if (path.endsWith("/index-inventory")) return json({ expected_index_fingerprint: fingerprint, document_manifest_hash: manifest, candidates: [], total_candidates: null, offset: 0, limit: 25, complete: false, reason_code: "inventory_unavailable", check_level: "report", store_checked: false });
      return json({ error: { code: "unexpected_route" } }, 404);
    }));
    mount();
    expect(await screen.findByRole("heading", { name: "Unable to check documents" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare documents" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

});
