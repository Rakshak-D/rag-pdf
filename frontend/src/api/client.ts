import type {
  AblationPlanQuery,
  AblationRequest,
  AblationSubmissionResponse,
  ApiErrorEnvelope,
  ArtifactListFilters,
  CatalogArtifactRecord,
  CatalogComparisonRecord,
  CatalogMetricRecord,
  EvaluationRequest,
  DocumentManifest,
  RunPlanPreparationRequest,
  RunPlanPreparationResponse,
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
