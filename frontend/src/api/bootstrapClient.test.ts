import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, BootstrapApiClient } from "./client";

const hash = "a".repeat(64);
const fingerprint = "b".repeat(64);
const status = { mode: "bootstrap", status: "running", accepting_jobs: true, worker_count: 1, queue_capacity: 2 };
const preview = { document_count: 2, manifest_hash: hash, index_fingerprint: fingerprint, run_fingerprint: "c".repeat(64), existing_index: false, fresh_build_eligible: true, reason_code: "fresh_build_available" };
const job = {
  job_id: "9de7bd41-7118-4cff-8eca-2d82bf8f7029", client_request_id: "client-request-000001", status: "queued",
  manifest_hash: hash, index_fingerprint: fingerprint, created_at: "2026-10-30T00:00:00+00:00", started_at: null,
  finished_at: null, error_code: null, build_id: null, report_reference: null, text_records: null, image_records: null, partial_persistence: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("BootstrapApiClient", () => {
  it("uses only the bootstrap proxy, authenticates protected calls, and validates status and preview", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ status: "ok", api_version: "v1" }))
      .mockResolvedValueOnce(Response.json(status))
      .mockResolvedValueOnce(Response.json(preview));
    const client = new BootstrapApiClient({ token: "separate-bootstrap-token", fetcher });
    await expect(client.getHealth()).resolves.toEqual({ status: "ok", api_version: "v1" });
    await expect(client.getStatus()).resolves.toEqual(status);
    await expect(client.preview({ resolved_config: { pdf_dir: "trusted-root" } })).resolves.toEqual(preview);
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "/bootstrap-api/health", "/bootstrap-api/v1/bootstrap/status", "/bootstrap-api/v1/bootstrap/preview",
    ]);
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).has("Authorization")).toBe(false);
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get("Authorization")).toBe("Bearer separate-bootstrap-token");
    expect(JSON.parse(String(fetcher.mock.calls[2]?.[1]?.body))).toEqual({ resolved_config: { pdf_dir: "trusted-root" } });
    expect(fetcher.mock.calls.every(([url]) => !String(url).includes("separate-bootstrap-token"))).toBe(true);
  });

  it("rejects a healthy wrong-service response and malformed bootstrap metadata", async () => {
    const wrongService = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ components: [] })) });
    await expect(wrongService.getStatus()).rejects.toMatchObject({ kind: "malformed_response" });
    const invalidPreview = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ...preview, existing_index: true })) });
    await expect(invalidPreview.preview({ resolved_config: {} })).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it("validates job submission identity and job-list/detail schemas", async () => {
    const submitted = { ...job, status: "queued" };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ job: submitted }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ jobs: [submitted], limit: 25, offset: 0 }))
      .mockResolvedValueOnce(Response.json({ job: submitted }));
    const client = new BootstrapApiClient({ token: "token", fetcher });
    const request = { resolved_config: { pdf_dir: "x" }, expected_manifest_hash: hash, expected_index_fingerprint: fingerprint, client_request_id: submitted.client_request_id };
    await expect(client.submitJob(request)).resolves.toMatchObject({ job: submitted });
    await expect(client.listJobs({ limit: 25, offset: 0 })).resolves.toMatchObject({ jobs: [submitted] });
    await expect(client.getJob(submitted.job_id)).resolves.toEqual(submitted);
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "/bootstrap-api/v1/bootstrap/jobs", "/bootstrap-api/v1/bootstrap/jobs?limit=25&offset=0", `/bootstrap-api/v1/bootstrap/jobs/${submitted.job_id}`,
    ]);
    const invalid = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ jobs: [{ ...submitted, report_reference: "C:\\private\\report.json" }], limit: 1, offset: 0 })) });
    await expect(invalid.listJobs({ limit: 1 })).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it("preserves safe API errors and distinguishes network and non-JSON proxy failures", async () => {
    const unauthorized = new BootstrapApiClient({ token: "wrong", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "authentication_required" } }, { status: 401 })) });
    await expect(unauthorized.getStatus()).rejects.toMatchObject({ kind: "http", status: 401, code: "authentication_required" });
    const network = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockRejectedValue(new Error("private address")) });
    await expect(network.getStatus()).rejects.toMatchObject({ kind: "network", code: "bootstrap_proxy_unavailable" });
    const html = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response("vite page", { headers: { "Content-Type": "text/html" } })) });
    await expect(html.getStatus()).rejects.toMatchObject({ kind: "non_json", code: "bootstrap_proxy_unavailable" });
    const unauthenticated = new BootstrapApiClient({ fetcher: vi.fn<typeof fetch>() });
    await expect(unauthenticated.getStatus()).rejects.toBeInstanceOf(ApiError);
  });
});
