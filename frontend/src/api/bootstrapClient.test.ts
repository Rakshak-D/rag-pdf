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
const reportCandidate = {
  candidate_id: "d".repeat(32), build_id: "d".repeat(32),
  report_reference: `build-artifacts/${"d".repeat(32)}/build-report.json`,
  compatibility: "report_match", reason_code: "report_matches",
  expected_index_fingerprint: fingerprint, document_manifest_hash: hash,
  reported_index_fingerprint: fingerprint, reported_document_manifest_hash: hash,
  report_status: "completed", reported_text_records: 14, reported_image_records: 2,
  check_level: "report", store_checked: false,
};
function inventoryPage(candidate: Record<string, unknown> = reportCandidate) {
  return {
    expected_index_fingerprint: fingerprint, document_manifest_hash: hash,
    candidates: [candidate], total_candidates: 1, offset: 0, limit: 25,
    complete: true, reason_code: "inventory_complete", check_level: "report", store_checked: false,
  };
}

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

  it("posts inventory through the bootstrap proxy with the preview manifest and bounded page", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(inventoryPage()));
    const client = new BootstrapApiClient({ token: "bootstrap-token", fetcher });
    const request = { resolved_config: { pdf_dir: "trusted" }, expected_manifest_hash: hash, limit: 25, offset: 0 };
    await expect(client.inspectIndexInventory(request, fingerprint)).resolves.toEqual(inventoryPage());
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("/bootstrap-api/v1/bootstrap/index-inventory");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer bootstrap-token");
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(String(url)).not.toContain("bootstrap-token");
  });

  it("accepts valid classifications, an empty complete page, and an explicitly incomplete page", async () => {
    const states = [
      reportCandidate,
      { ...reportCandidate, compatibility: "mismatch", reason_code: "index_fingerprint_mismatch", reported_index_fingerprint: "c".repeat(64), reported_text_records: null, reported_image_records: null },
      { ...reportCandidate, compatibility: "failed", reason_code: "build_failed", report_status: "failed", reported_text_records: null, reported_image_records: null },
      { ...reportCandidate, compatibility: "incomplete", reason_code: "partial_persistence", reported_text_records: null, reported_image_records: null },
      { ...reportCandidate, candidate_id: "entry-0123456789abcdef", build_id: null, report_reference: null, compatibility: "invalid", reason_code: "build_id_invalid", reported_index_fingerprint: null, reported_document_manifest_hash: null, report_status: null, reported_text_records: null, reported_image_records: null },
      { ...reportCandidate, candidate_id: "entry-fedcba9876543210", build_id: null, report_reference: null, compatibility: "unavailable", reason_code: "report_unavailable", reported_index_fingerprint: null, reported_document_manifest_hash: null, report_status: null, reported_text_records: null, reported_image_records: null },
    ];
    for (const candidate of states) {
      const client = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json(inventoryPage(candidate))) });
      await expect(client.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash, limit: 25, offset: 0 }, fingerprint)).resolves.toMatchObject({ candidates: [candidate] });
    }
    const empty = { ...inventoryPage(), candidates: [], total_candidates: 0, reason_code: "no_build_reports" };
    const emptyClient = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json(empty)) });
    await expect(emptyClient.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash, limit: 25, offset: 0 }, fingerprint)).resolves.toEqual(empty);
    const incomplete = { ...empty, total_candidates: null, complete: false, reason_code: "inventory_limit_exceeded" };
    const incompleteClient = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json(incomplete)) });
    await expect(incompleteClient.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash, limit: 25, offset: 0 }, fingerprint)).resolves.toEqual(incomplete);
  });

  it.each([
    ["malformed manifest", { ...inventoryPage(), document_manifest_hash: "bad" }],
    ["unexpected manifest", { ...inventoryPage(), document_manifest_hash: "e".repeat(64) }],
    ["unexpected fingerprint", { ...inventoryPage(), expected_index_fingerprint: "e".repeat(64) }],
    ["unknown classification", inventoryPage({ ...reportCandidate, compatibility: "ready" })],
    ["unsafe report reference", inventoryPage({ ...reportCandidate, report_reference: "../../secret.json" })],
    ["negative count", inventoryPage({ ...reportCandidate, reported_text_records: -1 })],
    ["count on mismatch", inventoryPage({ ...reportCandidate, compatibility: "mismatch" })],
    ["store checked", { ...inventoryPage(), store_checked: true }],
    ["unsupported check level", { ...inventoryPage(), check_level: "store" }],
    ["overlarge page", { ...inventoryPage(), candidates: [reportCandidate, reportCandidate], limit: 1 }],
    ["incomplete with omitted total", { ...inventoryPage(), complete: false, reason_code: "inventory_limit_exceeded" }],
  ])("rejects unsafe inventory response: %s", async (_name, response) => {
    const client = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json(response)) });
    await expect(client.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash, limit: 25, offset: 0 }, fingerprint)).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it("does not call the experiment API and handles inventory auth, non-JSON, and network failures", async () => {
    const paths: string[] = [];
    const client = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockImplementation(async (input) => {
      paths.push(String(input));
      return Response.json(inventoryPage());
    }) });
    await client.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash, limit: 25, offset: 0 }, fingerprint);
    expect(paths).toEqual(["/bootstrap-api/v1/bootstrap/index-inventory"]);

    const unauthorized = new BootstrapApiClient({ token: "bad", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "authentication_required" } }, { status: 401 })) });
    await expect(unauthorized.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash }, fingerprint)).rejects.toMatchObject({ status: 401 });
    const html = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response("not json", { headers: { "Content-Type": "text/html" } })) });
    await expect(html.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash }, fingerprint)).rejects.toMatchObject({ kind: "non_json" });
    const network = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")) });
    await expect(network.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash }, fingerprint)).rejects.toMatchObject({ kind: "network" });
    const malformedJson = new BootstrapApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response("{", { headers: { "Content-Type": "application/json" } })) });
    await expect(malformedJson.inspectIndexInventory({ resolved_config: {}, expected_manifest_hash: hash }, fingerprint)).rejects.toMatchObject({ kind: "malformed_response" });
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
