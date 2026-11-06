import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, WorkbenchApiClient } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("WorkbenchApiClient", () => {
  it("validates and returns the six ordered canonical ablation overrides", async () => {
    const variants = [
      { variant_id: "1_Dense_only", overrides: { retrieval_mode: "semantic", use_reranker: false } },
      { variant_id: "2_BM25_only", overrides: { retrieval_mode: "bm25", use_reranker: false } },
      { variant_id: "3_Hybrid_static", overrides: { retrieval_mode: "hybrid", adaptive_weighting: false } },
      { variant_id: "4_Hybrid_adaptive", overrides: { retrieval_mode: "hybrid", adaptive_weighting: true } },
      { variant_id: "5_Hybrid_reranker", overrides: { retrieval_mode: "hybrid", use_reranker: true } },
      { variant_id: "6_Full_pipeline", overrides: { retrieval_mode: "hybrid", use_filtering: true, percentile_cutoff: 80 } },
    ];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ schema_version: "1", variants }));
    const client = new WorkbenchApiClient({ token: "token", fetcher });
    await expect(client.listCanonicalAblationVariants()).resolves.toEqual({ schema_version: "1", variants });
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/ablations/variants");
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer token");

    const malformed = new WorkbenchApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ schema_version: "1", variants: variants.slice(1) })) });
    await expect(malformed.listCanonicalAblationVariants()).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it("keeps the bounded original result JSON and rejects oversized or malformed result envelopes", async () => {
    const original = '{"artifact_type":"evaluation_result","serialization_schema_version":"1","payload":{"status":"partial","value":0}}';
    const client = new WorkbenchApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response(original, { headers: { "Content-Type": "application/json" } })) });
    await expect(client.loadArtifactResult("a".repeat(64), "evaluation_result")).resolves.toMatchObject({
      artifact_type: "evaluation_result", payload: { status: "partial", value: 0 }, raw_json: original,
    });

    const oversized = new WorkbenchApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response("{}", { headers: { "Content-Type": "application/json", "Content-Length": String(9 * 1024 * 1024) } })) });
    await expect(oversized.loadArtifactResult("a".repeat(64), "evaluation_result")).rejects.toMatchObject({ code: "response_body_too_large" });

    const malformed = new WorkbenchApiClient({ token: "token", fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ artifact_type: "evaluation_result", serialization_schema_version: "1", payload: [] })) });
    await expect(malformed.loadArtifactResult("a".repeat(64), "evaluation_result")).rejects.toMatchObject({ kind: "malformed_response" });
  });

  it.each(["report_match", "mismatch", "unavailable", "indeterminate"] as const)("validates %s report compatibility", async (status) => {
    const hash = "a".repeat(64);
    const response = { status, reason_code: "report_checked", workflow: "evaluation", document_manifest_hash: hash, expected_index_fingerprint: "c".repeat(64), build_id: null, report_status: status === "report_match" ? "completed" : null, reported_text_records: status === "report_match" ? 3 : null, reported_image_records: status === "report_match" ? 0 : null, observed_text_records: null, observed_image_records: null, store_checked: false, check_level: "report" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(response));
    const client = new WorkbenchApiClient({ token: "token", fetcher });
    const request = { workflow: "evaluation" as const, resolved_config: {}, expected_manifest_hash: hash };
    await expect(client.checkIndexReadiness(request)).resolves.toEqual(response);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual(request);
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer token");
  });

  it("rejects readiness responses claiming store verification", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ status: "report_match", store_checked: true }));
    const client = new WorkbenchApiClient({ token: "token", fetcher });
    await expect(client.checkIndexReadiness({ workflow: "evaluation", resolved_config: {}, expected_manifest_hash: "a".repeat(64) })).rejects.toMatchObject({ kind: "malformed_response" });
  });
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

  it("validates Ask responses and sends the ordinary bearer only in the request header", async () => {
    const hash = "a".repeat(64);
    const response = {
      query_id: hash, question: "What is shown?", outcome: "answered", answer: "A supported answer.",
      model_reported_insufficient_evidence: false, failure_code: null, model_identifier: "local-answer",
      index_fingerprint: hash, document_manifest_hash: hash, run_fingerprint: hash, context_fingerprint: hash,
      selected_build_id: "b".repeat(32), selected_report_sha256: hash, selection_revision: 1,
      text_evidence: [], image_evidence: [], referenced_citations: [], warnings: [],
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(response));
    const client = new WorkbenchApiClient({ token: "ask-secret", fetcher });
    await expect(client.ask({ question: "What is shown?" })).resolves.toMatchObject({ answer: "A supported answer." });
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/ask");
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer ask-secret");
    expect(String(fetcher.mock.calls[0]?.[0])).not.toContain("ask-secret");
    await expect(client.ask({ question: "   " })).rejects.toMatchObject({ code: "question_invalid" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("rejects malformed Ask evidence and incomplete image responses", async () => {
    const malformedAsk = new WorkbenchApiClient({
      token: "ask-secret",
      fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ outcome: "answered", answer: "unvalidated" })),
    });
    await expect(malformedAsk.ask({ question: "question" })).rejects.toMatchObject({ kind: "malformed_response" });

    const incompleteImage = new WorkbenchApiClient({
      token: "ask-secret",
      fetcher: vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "image_evidence_expired" } }, { status: 404 })),
    });
    await expect(incompleteImage.getAskImage("c".repeat(32))).rejects.toMatchObject({ status: 404, code: "image_evidence_expired" });
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
