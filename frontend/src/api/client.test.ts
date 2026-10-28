import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, WorkbenchApiClient } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("WorkbenchApiClient", () => {
  it("validates manifest structure and prepares a plan with the inspected hash", async () => {
    const hash = "a".repeat(64);
    const manifest = { schema_version: "1", content_hash: hash, entry_count: 1, empty: false, entries: [{ relative_path: "paper.pdf", size_bytes: 7, sha256: "b".repeat(64), document_id: "doc_" + "c".repeat(64) }] };
    const plan = { plan_schema_version: "1", document_manifest_hash: hash, resolved_config_json: "{}", configuration_snapshot_hash: "b".repeat(64), index_fingerprint: "c".repeat(64), run_fingerprint: "d".repeat(64) };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(manifest)).mockResolvedValueOnce(Response.json({ run_plan: plan, manifest: { schema_version: "1", content_hash: hash, entry_count: 1 } }));
    const client = new WorkbenchApiClient({ token: "secret", fetcher });
    await expect(client.inspectDocuments()).resolves.toEqual(manifest);
    await expect(client.prepareRunPlan({ resolved_config: {}, expected_manifest_hash: hash })).resolves.toMatchObject({ run_plan: plan });
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ resolved_config: {}, expected_manifest_hash: hash });
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer secret");
  });

  it("rejects inconsistent manifest and malformed preparation responses", async () => {
    const hash = "a".repeat(64);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ schema_version: "1", content_hash: hash, entry_count: 1, empty: true, entries: [] })).mockResolvedValueOnce(Response.json({ run_plan: {}, manifest: {} }));
    const client = new WorkbenchApiClient({ token: "secret", fetcher });
    await expect(client.inspectDocuments()).rejects.toMatchObject({ kind: "malformed_response" });
    await expect(client.prepareRunPlan({ resolved_config: {}, expected_manifest_hash: hash })).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it("accepts an empty manifest and rejects malformed hashes", async () => {
    const hash = "a".repeat(64);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ schema_version: "1", content_hash: hash, entry_count: 0, empty: true, entries: [] })).mockResolvedValueOnce(Response.json({ schema_version: "1", content_hash: "bad", entry_count: 0, empty: true, entries: [] }));
    const client = new WorkbenchApiClient({ token: "secret", fetcher });
    await expect(client.inspectDocuments()).resolves.toMatchObject({ empty: true, entry_count: 0 });
    await expect(client.inspectDocuments()).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it("preserves stable preparation errors and reports network failures safely", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ error: { code: "manifest_changed" } }, { status: 409 })).mockRejectedValueOnce(new Error("private path"));
    const client = new WorkbenchApiClient({ token: "secret", fetcher });
    const request = { resolved_config: {}, expected_manifest_hash: "a".repeat(64) };
    await expect(client.prepareRunPlan(request)).rejects.toMatchObject({ kind: "http", status: 409, code: "manifest_changed" });
    await expect(client.prepareRunPlan(request)).rejects.toMatchObject({ kind: "network", code: "network_error" });
  });
  it("calls health without attaching a token", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: "ok", api_version: "v1" }));
    const client = new WorkbenchApiClient({ token: "secret-test-token", fetcher });

    await expect(client.getHealth()).resolves.toEqual({ status: "ok", api_version: "v1" });
    expect(fetcher).toHaveBeenCalledWith("/health", expect.objectContaining({ headers: expect.any(Headers) }));
    const init = fetcher.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });

  it("sends bearer authentication only to protected API routes", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ jobs: [] }));
    const client = new WorkbenchApiClient({ token: "local-secret", fetcher });

    await client.listJobs();
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer local-secret");
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/jobs");
  });

  it("parses a stable backend error envelope without exposing its body", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "authentication_required" } }, { status: 401 }));
    const client = new WorkbenchApiClient({ token: "wrong", fetcher });

    const rejection = client.getRuntimeModels();
    await expect(rejection).rejects.toBeInstanceOf(ApiError);
    await expect(rejection).rejects.toMatchObject({ kind: "http", status: 401, code: "authentication_required" });
  });

  it("distinguishes network failures and invalid JSON", async () => {
    const network = new WorkbenchApiClient({ fetcher: vi.fn<typeof fetch>().mockRejectedValue(new Error("private path")) });
    await expect(network.getHealth()).rejects.toMatchObject({ kind: "network", code: "network_error" });

    const invalid = new WorkbenchApiClient({
      fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response("not-json", { headers: { "Content-Type": "application/json" } })),
    });
    await expect(invalid.getHealth()).rejects.toMatchObject({ kind: "malformed_response", code: "malformed_response" });

    const html = new WorkbenchApiClient({ fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response("private response", { headers: { "Content-Type": "text/html" } })) });
    await expect(html.getHealth()).rejects.toMatchObject({ kind: "non_json", code: "non_json_response" });
  });

  it("sends JSON content type for JSON requests and discards unsafe error text", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "private path\nsecret" } }, { status: 400 }));
    const client = new WorkbenchApiClient({ token: "local-secret", fetcher });
    await expect(client.submitEvaluation({
      run_plan: {
        plan_schema_version: "1", document_manifest_hash: "manifest", resolved_config_json: "{}",
        configuration_snapshot_hash: "snapshot", index_fingerprint: "index", run_fingerprint: "run",
      },
      benchmark_suite: { benchmark_id: "synthetic", benchmark_version: "1", cases: [] },
    })).rejects.toMatchObject({
      kind: "http", code: "http_error", status: 400,
    });
    const init = fetcher.mock.calls[0]?.[1];
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(String(init?.body)).not.toContain("local-secret");
  });

  it("does not call protected routes without a token", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const client = new WorkbenchApiClient({ fetcher });
    await expect(client.listJobs()).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
