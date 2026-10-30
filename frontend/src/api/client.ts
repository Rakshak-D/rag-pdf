import type {
  AblationPlanQuery,
  AblationRequest,
  AblationSubmissionResponse,
  ApiErrorEnvelope,
  ArtifactListFilters,
  BootstrapInventoryCandidate,
  BootstrapInventoryPage,
  BootstrapInventoryRequest,
  BootstrapJobEnvelope,
  BootstrapJobRecord,
  BootstrapJobRequest,
  BootstrapJobsResponse,
  BootstrapPreviewRequest,
  BootstrapPreviewResponse,
  BootstrapStatusResponse,
  CatalogArtifactRecord,
  CatalogComparisonRecord,
  CatalogMetricRecord,
  EvaluationRequest,
  DocumentManifest,
  RunPlanPreparationRequest,
  RunPlanPreparationResponse,
  IndexReadinessRequest,
  IndexReadinessResponse,
  EvaluationSubmissionResponse,
  ExperimentJobRecord,
  HealthResponse,
  JobEnvelope,
  JobListFilters,
  ModelComponentStatus,
  ModelState,
  ResultStatus,
  RuntimeModelsResponse,
} from "./types";

export type ApiErrorKind = "network" | "non_json" | "http" | "malformed_response";

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string;
  readonly kind: ApiErrorKind;

  constructor(kind: ApiErrorKind, code: string, status: number | null = null) {
    super(code);
    this.name = "ApiError";
    this.kind = kind;
    this.code = code;
    this.status = status;
  }
}

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function hasStrings(value: Record<string, unknown>, fields: readonly string[]): boolean {
  return fields.every((field) => typeof value[field] === "string");
}

const sha256 = (value: unknown): value is string => typeof value === "string" && /^[a-fA-F0-9]{64}$/.test(value);
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

function isManifest(value: unknown): value is DocumentManifest {
  return isRecord(value) && typeof value.schema_version === "string" && sha256(value.content_hash) &&
    count(value.entry_count) && typeof value.empty === "boolean" && Array.isArray(value.entries) &&
    value.entry_count === value.entries.length && value.empty === (value.entry_count === 0) &&
    value.entries.every((entry: unknown) => isRecord(entry) && typeof entry.relative_path === "string" &&
      entry.relative_path.length > 0 && !entry.relative_path.startsWith("/") && !entry.relative_path.includes("\\") &&
      !entry.relative_path.split("/").some((part: string) => part === ".." || part === "." || part === "") &&
      !/^[A-Za-z]:/.test(entry.relative_path) && count(entry.size_bytes) && sha256(entry.sha256) &&
      typeof entry.document_id === "string" && /^doc_[a-f0-9]{64}$/.test(entry.document_id));
}

function isPreparedPlan(value: unknown): value is RunPlanPreparationResponse {
  if (!isRecord(value) || !isRecord(value.run_plan) || !isRecord(value.manifest)) return false;
  const plan = value.run_plan;
  const manifest = value.manifest;
  return typeof plan.plan_schema_version === "string" && typeof plan.resolved_config_json === "string" &&
    sha256(plan.document_manifest_hash) && sha256(plan.configuration_snapshot_hash) && sha256(plan.index_fingerprint) &&
    sha256(plan.run_fingerprint) && typeof manifest.schema_version === "string" && sha256(manifest.content_hash) &&
    count(manifest.entry_count) && manifest.entry_count > 0 && plan.document_manifest_hash === manifest.content_hash;
}

function isIndexReadiness(value: unknown): value is IndexReadinessResponse {
  if (!isRecord(value)) return false;
  if (!["report_match", "mismatch", "unavailable", "indeterminate"].includes(String(value.status)) ||
    (value.workflow !== "evaluation" && value.workflow !== "ablation") ||
    typeof value.reason_code !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(value.reason_code) ||
    !sha256(value.document_manifest_hash) || !sha256(value.expected_index_fingerprint) ||
    !isNullableString(value.build_id) || !isNullableString(value.report_status) ||
    !(value.reported_text_records === null || count(value.reported_text_records)) ||
    !(value.reported_image_records === null || count(value.reported_image_records)) ||
    value.observed_text_records !== null || value.observed_image_records !== null ||
    value.store_checked !== false || value.check_level !== "report") return false;
  return value.status === "report_match"
    ? value.reported_text_records !== null && value.reported_image_records !== null && (value.report_status === "completed" || value.report_status === "completed_with_warnings")
    : value.reported_text_records === null && value.reported_image_records === null && value.report_status === null && value.build_id === null;
}

function isModelStatus(value: unknown): value is ModelComponentStatus {
  if (!isRecord(value)) return false;
  return (
    (value.component_kind === "text" || value.component_kind === "image" || value.component_kind === "reranker") &&
    typeof value.model_identifier === "string" &&
    (value.state === "unloaded" || value.state === "loading" || value.state === "ready" || value.state === "failed") &&
    isNullableString(value.error_code) &&
    typeof value.explicitly_warmed === "boolean"
  );
}

function isJob(value: unknown): value is ExperimentJobRecord {
  if (!isRecord(value) || !hasStrings(value, ["job_id", "job_type", "status", "created_at", "benchmark_id", "benchmark_version"])) return false;
  const statuses = ["queued", "running", "succeeded", "partial", "failed", "cancelled", "interrupted"];
  return (
    (value.job_type === "evaluation" || value.job_type === "ablation") &&
    statuses.includes(String(value.status)) &&
    ["started_at", "finished_at", "run_fingerprint", "shared_index_fingerprint", "document_manifest_hash", "artifact_id", "error_code", "result_status"].every((key) => isNullableString(value[key])) &&
    (value.result_status === null || value.result_status === "succeeded" || value.result_status === "partial" || value.result_status === "failed")
  );
}

function isArtifact(value: unknown): value is CatalogArtifactRecord {
  if (!isRecord(value) || !hasStrings(value, ["artifact_id", "artifact_type", "relative_path", "serialization_schema_version", "benchmark_id", "benchmark_version", "status"])) return false;
  const status = value.status === "succeeded" || value.status === "partial" || value.status === "failed";
  const counts = ["case_count", "succeeded_case_count", "failed_case_count"].every((key) => value[key] === null || (typeof value[key] === "number" && Number.isInteger(value[key])));
  return (
    (value.artifact_type === "evaluation_result" || value.artifact_type === "ablation_result") && status &&
    typeof value.byte_length === "number" && Number.isInteger(value.byte_length) &&
    ["run_fingerprint", "evaluation_fingerprint", "document_manifest_hash", "shared_index_fingerprint"].every((key) => isNullableString(value[key])) && counts
  );
}

function isMetric(value: unknown): value is CatalogMetricRecord {
  if (!isRecord(value) || !hasStrings(value, ["artifact_id", "variant_id", "metric_id", "metric_name", "status", "unit", "optimization_direction", "reference_kind", "implementation_version"])) return false;
  return (
    (value.status === "computed" || value.status === "partial" || value.status === "no_eligible_values") &&
    (value.optimization_direction === "maximize" || value.optimization_direction === "minimize" || value.optimization_direction === "neutral") &&
    isNullableNumber(value.value) &&
    ["eligible_count", "skipped_count", "failed_count", "unavailable_count"].every((key) => typeof value[key] === "number" && Number.isInteger(value[key])) &&
    isNullableString(value.model_identifier) && isNullableString(value.methodology_note)
  );
}

function isComparison(value: unknown): value is CatalogComparisonRecord {
  return isRecord(value) && hasStrings(value, ["artifact_id", "metric_id", "state"]) &&
    (value.state === "comparable" || value.state === "incomplete" || value.state === "not_comparable") &&
    isNullableString(value.reason_code);
}

function isArtifactStatus(value: unknown): value is CatalogArtifactRecord["status"] {
  return value === "succeeded" || value === "partial" || value === "failed";
}

function malformed(status: number | null = null): never {
  throw new ApiError("malformed_response", "malformed_response", status);
}

async function parseJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new ApiError("non_json", "non_json_response", response.status);
  }
  try {
    return await response.json() as unknown;
  } catch {
    throw new ApiError("malformed_response", "malformed_response", response.status);
  }
}

function errorCode(payload: unknown): string | null {
  if (!isRecord(payload) || !isRecord(payload.error) || typeof payload.error.code !== "string") return null;
  return /^[a-z][a-z0-9_]{0,63}$/.test(payload.error.code) ? payload.error.code : null;
}

function appendQuery(path: string, params: URLSearchParams): string {
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function paramsFrom<T extends object>(values: T): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) params.set(key, String(value));
  }
  return params;
}

export class WorkbenchApiClient {
  private readonly token: string | null;
  private readonly baseUrl: string;
  private readonly fetcher: FetchLike;

  constructor(options: { token?: string | null; baseUrl?: string; fetcher?: FetchLike } = {}) {
    this.token = options.token ?? null;
    this.baseUrl = options.baseUrl ?? "";
    this.fetcher = options.fetcher ?? fetch;
  }

  private async request(path: string, options: { method?: string; body?: unknown; protected?: boolean } = {}): Promise<unknown> {
    const headers = new Headers();
    if (options.protected !== false && path.startsWith("/api/v1/")) {
      if (!this.token) throw new ApiError("http", "authentication_required", 401);
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    const init: RequestInit = { method: options.method ?? "GET", headers };
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    }
    const requestUrl = this.baseUrl ? new URL(path, this.baseUrl).toString() : path;
    let response: Response;
    try {
      response = await this.fetcher(requestUrl, init);
    } catch {
      throw new ApiError("network", "network_error");
    }
    const payload = await parseJson(response);
    if (!response.ok) {
      const code = errorCode(payload);
      throw new ApiError("http", code ?? "http_error", response.status);
    }
    return payload;
  }

  async getHealth(): Promise<HealthResponse> {
    const value = await this.request("/health", { protected: false });
    if (!isRecord(value) || value.status !== "ok" || value.api_version !== "v1") malformed();
    return { status: "ok", api_version: "v1" };
  }

  async inspectDocuments(): Promise<DocumentManifest> {
    const value = await this.request("/api/v1/documents/manifest");
    if (!isManifest(value)) malformed();
    return value;
  }

  async prepareRunPlan(input: RunPlanPreparationRequest): Promise<RunPlanPreparationResponse> {
    const value = await this.request("/api/v1/run-plans", { method: "POST", body: input });
    if (!isPreparedPlan(value) || value.manifest.content_hash !== input.expected_manifest_hash.toLowerCase()) malformed();
    return value;
  }

  async checkIndexReadiness(input: IndexReadinessRequest): Promise<IndexReadinessResponse> {
    const value = await this.request("/api/v1/index/readiness", { method: "POST", body: input });
    if (!isIndexReadiness(value) || value.workflow !== input.workflow || value.document_manifest_hash !== input.expected_manifest_hash.toLowerCase()) malformed();
    return value;
  }

  async getRuntimeModels(): Promise<RuntimeModelsResponse> {
    const value = await this.request("/api/v1/runtime/models");
    if (!isRecord(value) || !Array.isArray(value.components) || !value.components.every(isModelStatus)) malformed();
    return { components: value.components };
  }

  async listJobs(filters: JobListFilters = {}): Promise<ExperimentJobRecord[]> {
    const value = await this.request(appendQuery("/api/v1/jobs", paramsFrom(filters)));
    if (!isRecord(value) || !Array.isArray(value.jobs) || !value.jobs.every(isJob)) malformed();
    return value.jobs;
  }

  async getJob(jobId: string): Promise<ExperimentJobRecord> {
    const value = await this.request(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    if (!isRecord(value) || !isJob(value.job)) malformed();
    return value.job;
  }

  async cancelJob(jobId: string): Promise<ExperimentJobRecord> {
    const value = await this.request(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
    if (!isRecord(value) || !isJob(value.job)) malformed();
    return value.job;
  }

  async listArtifacts(filters: ArtifactListFilters = {}): Promise<CatalogArtifactRecord[]> {
    const value = await this.request(appendQuery("/api/v1/artifacts", paramsFrom(filters)));
    if (!isRecord(value) || !Array.isArray(value.artifacts) || !value.artifacts.every(isArtifact)) malformed();
    return value.artifacts;
  }

  async getArtifact(artifactId: string): Promise<CatalogArtifactRecord> {
    const value = await this.request(`/api/v1/artifacts/${encodeURIComponent(artifactId)}`);
    if (!isArtifact(value)) malformed();
    return value;
  }

  async listMetrics(artifactId: string, variantId?: string): Promise<CatalogMetricRecord[]> {
    const params = new URLSearchParams();
    if (variantId !== undefined) params.set("variant_id", variantId);
    const value = await this.request(appendQuery(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/metrics`, params));
    if (!isRecord(value) || !Array.isArray(value.metrics) || !value.metrics.every(isMetric)) malformed();
    return value.metrics;
  }

  async listComparisons(artifactId: string): Promise<CatalogComparisonRecord[]> {
    const value = await this.request(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/comparisons`);
    if (!isRecord(value) || !Array.isArray(value.comparisons) || !value.comparisons.every(isComparison)) malformed();
    return value.comparisons;
  }

  async loadArtifactResult(artifactId: string, plan?: AblationPlanQuery): Promise<unknown> {
    const params = new URLSearchParams();
    if (plan) {
      params.set("base_config", JSON.stringify(plan.base_config));
      params.set("document_manifest_hash", plan.document_manifest_hash);
    }
    return this.request(appendQuery(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/result`, params));
  }

  async submitEvaluationJob(input: EvaluationRequest): Promise<JobEnvelope> {
    return this.postJob("/api/v1/jobs/evaluations", input);
  }

  async submitAblationJob(input: AblationRequest): Promise<JobEnvelope> {
    return this.postJob("/api/v1/jobs/ablations", input);
  }

  private async postJob(path: string, input: EvaluationRequest | AblationRequest): Promise<JobEnvelope> {
    const value = await this.request(path, { method: "POST", body: input });
    if (!isRecord(value) || !isJob(value.job)) malformed();
    return { job: value.job };
  }

  async submitEvaluation(input: EvaluationRequest): Promise<EvaluationSubmissionResponse> {
    const value = await this.request("/api/v1/evaluations", { method: "POST", body: input });
    if (!isRecord(value) || !isArtifact(value.artifact) || !isRecord(value.result) || !hasStrings(value.result, ["benchmark_id", "benchmark_version", "status", "run_fingerprint", "evaluation_fingerprint"])) malformed();
    if (!isArtifactStatus(value.result.status)) malformed();
    return {
      artifact: value.artifact,
      result: {
        benchmark_id: value.result.benchmark_id as string,
        benchmark_version: value.result.benchmark_version as string,
        status: value.result.status,
        run_fingerprint: value.result.run_fingerprint as string,
        evaluation_fingerprint: value.result.evaluation_fingerprint as string,
      },
    };
  }

  async submitAblation(input: AblationRequest): Promise<AblationSubmissionResponse> {
    const value = await this.request("/api/v1/ablations", { method: "POST", body: input });
    if (!isRecord(value) || !isArtifact(value.artifact) || !isRecord(value.result) ||
      !isArtifactStatus(value.result.status) ||
      !hasStrings(value.result, ["benchmark_id", "benchmark_version", "document_manifest_hash", "shared_index_fingerprint"]) ||
      !Array.isArray(value.result.variants) || !value.result.variants.every((variant: unknown) => isRecord(variant) &&
        hasStrings(variant, ["variant_id", "status"]) && isArtifactStatus(variant.status) && isNullableString(variant.error_code)) ||
      !Array.isArray(value.result.comparisons) || !value.result.comparisons.every((comparison: unknown) => isRecord(comparison) &&
        hasStrings(comparison, ["metric_id", "state"]) &&
        (comparison.state === "comparable" || comparison.state === "incomplete" || comparison.state === "not_comparable") &&
        isNullableString(comparison.reason_code))) malformed();
    return {
      artifact: value.artifact,
      result: {
        status: value.result.status,
        benchmark_id: value.result.benchmark_id as string,
        benchmark_version: value.result.benchmark_version as string,
        document_manifest_hash: value.result.document_manifest_hash as string,
        shared_index_fingerprint: value.result.shared_index_fingerprint as string,
        variants: value.result.variants.map((variant: unknown) => {
          if (!isRecord(variant)) malformed();
          return { variant_id: variant.variant_id as string, status: variant.status as CatalogArtifactRecord["status"], error_code: variant.error_code as string | null };
        }),
        comparisons: value.result.comparisons.map((comparison: unknown) => {
          if (!isRecord(comparison)) malformed();
          return { metric_id: comparison.metric_id as string, state: comparison.state as CatalogComparisonRecord["state"], reason_code: comparison.reason_code as string | null };
        }),
      },
    };
  }
}

export type { ApiErrorEnvelope, ModelState, ResultStatus };

const bootstrapStatuses = ["queued", "running", "succeeded", "failed", "interrupted"] as const;

function isBootstrapStatus(value: unknown): value is BootstrapStatusResponse {
  return isRecord(value) && value.mode === "bootstrap" && value.status === "running" &&
    typeof value.accepting_jobs === "boolean" && value.worker_count === 1 &&
    Number.isInteger(value.queue_capacity) && Number(value.queue_capacity) >= 1 && Number(value.queue_capacity) <= 32;
}

function isBootstrapPreview(value: unknown): value is BootstrapPreviewResponse {
  return isRecord(value) && Number.isInteger(value.document_count) && Number(value.document_count) >= 1 &&
    Number(value.document_count) <= 1_000_000 && sha256(value.manifest_hash) &&
    sha256(value.index_fingerprint) && sha256(value.run_fingerprint) &&
    typeof value.existing_index === "boolean" && typeof value.fresh_build_eligible === "boolean" &&
    value.fresh_build_eligible === !value.existing_index &&
    typeof value.reason_code === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(value.reason_code) &&
    ((value.existing_index && value.reason_code === "index_already_exists") ||
      (!value.existing_index && value.reason_code === "fresh_build_available"));
}

const inventoryStates = ["report_match", "mismatch", "failed", "incomplete", "invalid", "unavailable"] as const;
const inventoryCandidateKeys = [
  "candidate_id", "build_id", "report_reference", "compatibility", "reason_code",
  "expected_index_fingerprint", "document_manifest_hash", "reported_index_fingerprint",
  "reported_document_manifest_hash", "report_status", "reported_text_records",
  "reported_image_records", "check_level", "store_checked",
] as const;
const inventoryPageKeys = [
  "expected_index_fingerprint", "document_manifest_hash", "candidates", "total_candidates",
  "offset", "limit", "complete", "reason_code", "check_level", "store_checked",
] as const;

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function isInventoryCandidate(value: unknown, expectedFingerprint: string, manifestHash: string): value is BootstrapInventoryCandidate {
  if (!isRecord(value) || !hasExactKeys(value, inventoryCandidateKeys) ||
    typeof value.candidate_id !== "string" || !/^(?:[0-9a-f]{32}|entry-[0-9a-f]{16})$/.test(value.candidate_id) ||
    !isNullableString(value.build_id) || (value.build_id !== null && !/^[0-9a-f]{32}$/.test(value.build_id)) ||
    !isNullableString(value.report_reference) ||
    (value.report_reference !== null && !/^build-artifacts\/[0-9a-f]{32}\/build-report\.json$/.test(value.report_reference)) ||
    !inventoryStates.includes(value.compatibility as typeof inventoryStates[number]) ||
    typeof value.reason_code !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(value.reason_code) ||
    typeof value.expected_index_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(value.expected_index_fingerprint) ||
    typeof value.document_manifest_hash !== "string" || !/^[0-9a-f]{64}$/.test(value.document_manifest_hash) ||
    !isNullableString(value.reported_index_fingerprint) || (value.reported_index_fingerprint !== null && !/^[0-9a-f]{64}$/.test(value.reported_index_fingerprint)) ||
    !isNullableString(value.reported_document_manifest_hash) || (value.reported_document_manifest_hash !== null && !/^[0-9a-f]{64}$/.test(value.reported_document_manifest_hash)) ||
    !(value.report_status === null || value.report_status === "completed" || value.report_status === "completed_with_warnings" || value.report_status === "failed") ||
    !(value.reported_text_records === null || count(value.reported_text_records)) ||
    !(value.reported_image_records === null || count(value.reported_image_records)) ||
    value.check_level !== "report" || value.store_checked !== false ||
    value.expected_index_fingerprint !== expectedFingerprint || value.document_manifest_hash !== manifestHash) return false;

  if (value.build_id === null) {
    if (value.report_reference !== null || !/^entry-[0-9a-f]{16}$/.test(value.candidate_id)) return false;
  } else if (value.candidate_id !== value.build_id || value.report_reference !== `build-artifacts/${value.build_id}/build-report.json`) return false;

  if (value.compatibility === "report_match") {
    return value.reported_index_fingerprint === expectedFingerprint &&
      value.reported_document_manifest_hash === manifestHash &&
      (value.report_status === "completed" || value.report_status === "completed_with_warnings") &&
      value.reported_text_records !== null && value.reported_image_records !== null && value.build_id !== null;
  }
  return value.reported_text_records === null && value.reported_image_records === null &&
    (value.compatibility !== "failed" || value.report_status === "failed");
}

function isInventoryPage(
  value: unknown,
  expectedManifestHash: string,
  expectedIndexFingerprint: string,
  limit: number,
  offset: number,
): value is BootstrapInventoryPage {
  if (!isRecord(value) || !hasExactKeys(value, inventoryPageKeys) ||
    typeof value.expected_index_fingerprint !== "string" || !/^[0-9a-f]{64}$/.test(value.expected_index_fingerprint) ||
    typeof value.document_manifest_hash !== "string" || !/^[0-9a-f]{64}$/.test(value.document_manifest_hash) ||
    !Array.isArray(value.candidates) || value.candidates.length > limit ||
    !(value.total_candidates === null || (count(value.total_candidates) && value.total_candidates <= 512)) ||
    value.offset !== offset || value.limit !== limit || typeof value.complete !== "boolean" ||
    typeof value.reason_code !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(value.reason_code) ||
    value.check_level !== "report" || value.store_checked !== false ||
    value.document_manifest_hash !== expectedManifestHash || value.expected_index_fingerprint !== expectedIndexFingerprint ||
    !value.candidates.every((candidate: unknown) => isInventoryCandidate(candidate, expectedIndexFingerprint, expectedManifestHash))) return false;

  if (value.complete) {
    return value.total_candidates !== null &&
      value.candidates.length === Math.min(limit, Math.max(0, value.total_candidates - offset));
  }
  return value.total_candidates === null && value.candidates.length === 0;
}

function isBootstrapJob(value: unknown): value is BootstrapJobRecord {
  if (!isRecord(value) || !hasStrings(value, ["job_id", "client_request_id", "status", "manifest_hash", "index_fingerprint", "created_at"])) return false;
  if (typeof value.job_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.job_id) ||
    typeof value.client_request_id !== "string" || !/^[A-Za-z0-9_-]{16,80}$/.test(value.client_request_id) ||
    !bootstrapStatuses.includes(value.status as typeof bootstrapStatuses[number]) ||
    !sha256(value.manifest_hash) || !sha256(value.index_fingerprint) ||
    !isNullableString(value.started_at) || !isNullableString(value.finished_at) ||
    !isNullableString(value.error_code) || (value.error_code !== null && !/^[a-z][a-z0-9_]{0,63}$/.test(value.error_code)) ||
    !isNullableString(value.build_id) || (value.build_id !== null && !/^[0-9a-f]{32}$/.test(value.build_id)) ||
    !isNullableString(value.report_reference) ||
    (value.report_reference !== null && !/^build-artifacts\/[0-9a-f]{32}\/build-report\.json$/.test(value.report_reference)) ||
    !(value.text_records === null || count(value.text_records)) ||
    !(value.image_records === null || count(value.image_records)) ||
    !(value.partial_persistence === null || typeof value.partial_persistence === "boolean")) return false;
  return value.status === "succeeded"
    ? value.build_id !== null && value.report_reference !== null && value.text_records !== null && value.image_records !== null && value.partial_persistence === false
    : value.report_reference === null;
}

/** Client for the separate bootstrap-only service. Its fixed prefix is routed
 * by Vite to the trusted bootstrap target; it never falls back to `/api`. */
export class BootstrapApiClient {
  private readonly token: string | null;
  private readonly fetcher: FetchLike;
  private readonly prefix: string;

  constructor(options: { token?: string | null; fetcher?: FetchLike; prefix?: string } = {}) {
    this.token = options.token ?? null;
    this.fetcher = options.fetcher ?? fetch;
    this.prefix = options.prefix ?? "/bootstrap-api";
  }

  private async request(path: string, options: { method?: string; body?: unknown; protected?: boolean } = {}): Promise<unknown> {
    const headers = new Headers();
    if (options.protected !== false) {
      if (!this.token) throw new ApiError("http", "authentication_required", 401);
      headers.set("Authorization", `Bearer ${this.token}`);
    }
    const init: RequestInit = { method: options.method ?? "GET", headers };
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    }
    let response: Response;
    try {
      response = await this.fetcher(`${this.prefix}${path}`, init);
    } catch {
      throw new ApiError("network", "bootstrap_proxy_unavailable");
    }
    let payload: unknown;
    try {
      payload = await parseJson(response);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "non_json") {
        throw new ApiError("non_json", "bootstrap_proxy_unavailable", response.status);
      }
      throw error;
    }
    if (!response.ok) {
      throw new ApiError("http", errorCode(payload) ?? "http_error", response.status);
    }
    return payload;
  }

  async getHealth(): Promise<HealthResponse> {
    const value = await this.request("/health", { protected: false });
    if (!isRecord(value) || value.status !== "ok" || value.api_version !== "v1") malformed();
    return { status: "ok", api_version: "v1" };
  }

  async getStatus(): Promise<BootstrapStatusResponse> {
    const value = await this.request("/v1/bootstrap/status");
    if (!isBootstrapStatus(value)) malformed();
    return value;
  }

  async preview(input: BootstrapPreviewRequest): Promise<BootstrapPreviewResponse> {
    const value = await this.request("/v1/bootstrap/preview", { method: "POST", body: input });
    if (!isBootstrapPreview(value)) malformed();
    return value;
  }

  async inspectIndexInventory(
    input: BootstrapInventoryRequest,
    expectedIndexFingerprint: string,
  ): Promise<BootstrapInventoryPage> {
    const limit = input.limit ?? 50;
    const offset = input.offset ?? 0;
    if (!/^[0-9a-f]{64}$/.test(input.expected_manifest_hash) || !/^[0-9a-f]{64}$/.test(expectedIndexFingerprint) ||
      !Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 512) {
      throw new ApiError("malformed_response", "inventory_request_invalid");
    }
    const body: BootstrapInventoryRequest = {
      resolved_config: input.resolved_config,
      expected_manifest_hash: input.expected_manifest_hash,
      limit,
      offset,
    };
    const value = await this.request("/v1/bootstrap/index-inventory", { method: "POST", body });
    if (isRecord(value) && typeof value.document_manifest_hash === "string" &&
      /^[0-9a-f]{64}$/.test(value.document_manifest_hash) && value.document_manifest_hash !== input.expected_manifest_hash) {
      throw new ApiError("malformed_response", "inventory_identity_changed");
    }
    if (isRecord(value) && typeof value.expected_index_fingerprint === "string" &&
      /^[0-9a-f]{64}$/.test(value.expected_index_fingerprint) && value.expected_index_fingerprint !== expectedIndexFingerprint) {
      throw new ApiError("malformed_response", "inventory_identity_changed");
    }
    if (!isInventoryPage(value, input.expected_manifest_hash, expectedIndexFingerprint, limit, offset)) {
      throw new ApiError("malformed_response", "inventory_response_invalid");
    }
    return value;
  }

  async submitJob(input: BootstrapJobRequest): Promise<BootstrapJobEnvelope> {
    if (!/^[A-Za-z0-9_-]{16,80}$/.test(input.client_request_id)) {
      throw new ApiError("malformed_response", "request_id_invalid");
    }
    const value = await this.request("/v1/bootstrap/jobs", { method: "POST", body: input });
    if (!isRecord(value) || !isBootstrapJob(value.job)) malformed();
    if (value.job.client_request_id !== input.client_request_id ||
      value.job.manifest_hash !== input.expected_manifest_hash.toLowerCase() ||
      value.job.index_fingerprint !== input.expected_index_fingerprint.toLowerCase()) malformed();
    return { job: value.job };
  }

  async listJobs(options: { limit?: number; offset?: number } = {}): Promise<BootstrapJobsResponse> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 1_000_000) {
      throw new ApiError("malformed_response", "pagination_invalid");
    }
    const value = await this.request(`/v1/bootstrap/jobs?limit=${limit}&offset=${offset}`);
    if (!isRecord(value) || !Array.isArray(value.jobs) || !value.jobs.every(isBootstrapJob) ||
      value.limit !== limit || value.offset !== offset || value.jobs.length > limit) malformed();
    return value as unknown as BootstrapJobsResponse;
  }

  async getJob(jobId: string): Promise<BootstrapJobRecord> {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(jobId)) {
      throw new ApiError("http", "job_not_found", 404);
    }
    const value = await this.request(`/v1/bootstrap/jobs/${encodeURIComponent(jobId)}`);
    if (!isRecord(value) || !isBootstrapJob(value.job) || value.job.job_id !== jobId) malformed();
    return value.job;
  }
}
