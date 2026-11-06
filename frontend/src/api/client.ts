import type {
  AblationPlanQuery,
  AblationRequest,
  AblationSubmissionResponse,
  AblationVariantCatalog,
  AskRequest,
  AskResponse,
  ChatModelCatalog,
  ApiErrorEnvelope,
  ArtifactListFilters,
  BootstrapInventoryCandidate,
  BootstrapInventoryPage,
  BootstrapInventoryRequest,
  BootstrapDocumentImportResponse,
  BootstrapDocumentRecord,
  BootstrapDocumentsResponse,
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
  IndexSelectionIdentity,
  IndexSelectionRequest,
  IndexSelectionState,
  EvaluationSubmissionResponse,
  ExperimentJobRecord,
  HealthResponse,
  GeneralChatRequest,
  GeneralChatResponse,
  JobEnvelope,
  JobListFilters,
  ModelComponentStatus,
  ModelState,
  ResultStatus,
  RuntimeModelsResponse,
  StoredArtifactResult,
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

function isIndexSelectionIdentity(value: unknown): value is IndexSelectionIdentity {
  return isRecord(value) && typeof value.build_id === "string" && /^[0-9a-f]{32}$/.test(value.build_id) &&
    value.report_reference === `build-artifacts/${value.build_id}/build-report.json` &&
    sha256(value.document_manifest_hash) && sha256(value.index_fingerprint) && sha256(value.report_sha256) &&
    sha256(value.configuration_snapshot_hash) && sha256(value.run_fingerprint) &&
    (value.report_status === "completed" || value.report_status === "completed_with_warnings") &&
    count(value.selection_revision) && value.selection_revision > 0;
}

function isIndexSelectionState(value: unknown): value is IndexSelectionState {
  if (!isRecord(value) || !["unselected", "selected", "stale", "invalid", "legacy_configured"].includes(String(value.selection_status)) ||
    !(value.revision === null || count(value.revision)) || !(value.selection === null || isIndexSelectionIdentity(value.selection)) ||
    !(value.legacy_report_reference === null || (typeof value.legacy_report_reference === "string" && /^build-artifacts\/[0-9a-f]{32}\/build-report\.json$/.test(value.legacy_report_reference))) ||
    typeof value.reason_code !== "string" || !/^[a-z][a-z0-9_]{1,63}$/.test(value.reason_code) ||
    value.check_level !== "report" || value.store_checked !== false) return false;
  if (value.selection_status === "selected" || value.selection_status === "stale") {
    return isIndexSelectionIdentity(value.selection) && value.revision === value.selection.selection_revision && value.legacy_report_reference === null;
  }
  if (value.selection !== null) return false;
  if (value.selection_status === "invalid") return value.revision === null;
  if (value.selection_status === "legacy_configured") {
    return value.revision === 0 && typeof value.legacy_report_reference === "string";
  }
  return value.revision === 0 && value.legacy_report_reference === null;
}

function isAskTextEvidence(value: unknown): boolean {
  return isRecord(value) && typeof value.label === "string" && /^T[1-9][0-9]{0,2}$/.test(value.label) &&
    typeof value.document_id === "string" && /^doc_[a-f0-9]{64}$/.test(value.document_id) &&
    typeof value.source_path === "string" && value.source_path.length > 0 && !value.source_path.startsWith("/") &&
    !value.source_path.includes("\\") && !value.source_path.split("/").some((part) => part === ".." || part === "." || part === "") &&
    value.source_path.toLowerCase().endsWith(".pdf") && (value.page_number === null || (Number.isInteger(value.page_number) && Number(value.page_number) > 0)) &&
    typeof value.passage === "string" && value.passage.length > 0 && value.passage.length <= 32_000 &&
    isNullableString(value.record_id) && typeof value.truncated === "boolean";
}

function isAskImageEvidence(value: unknown): boolean {
  if (!isRecord(value) || typeof value.label !== "string" || !/^I[1-9][0-9]{0,2}$/.test(value.label) ||
    typeof value.document_id !== "string" || !/^doc_[a-f0-9]{64}$/.test(value.document_id) ||
    typeof value.source_path !== "string" || value.source_path.length === 0 || value.source_path.startsWith("/") ||
    value.source_path.includes("\\") || value.source_path.split("/").some((part) => part === ".." || part === "." || part === "") ||
    !value.source_path.toLowerCase().endsWith(".pdf") || !(value.page_number === null || (Number.isInteger(value.page_number) && Number(value.page_number) > 0)) ||
    typeof value.caption !== "string" || value.caption.length > 2000 || !isNullableString(value.record_id)) return false;
  const reference = value.image_reference;
  const contentType = value.content_type;
  const size = value.size_bytes;
  if (reference === null && contentType === null && size === null) return true;
  return typeof reference === "string" && /^[0-9a-f]{32}$/.test(reference) &&
    (contentType === "image/png" || contentType === "image/jpeg" || contentType === "image/webp") &&
    typeof size === "number" && Number.isInteger(size) && size > 0 && size <= 8 * 1024 * 1024;
}

function isAskResponse(value: unknown): value is AskResponse {
  if (!isRecord(value) || !sha256(value.query_id) || typeof value.question !== "string" || value.question.length < 1 || value.question.length > 4000 ||
    (value.outcome !== "answered" && value.outcome !== "insufficient_evidence") ||
    !isNullableString(value.answer) || (typeof value.answer === "string" && value.answer.length > 64 * 1024) ||
    !isNullableString(value.failure_code) || (typeof value.failure_code === "string" && !/^[a-z][a-z0-9_]{1,63}$/.test(value.failure_code)) ||
    !isNullableString(value.model_identifier) || (typeof value.model_identifier === "string" && (value.model_identifier.length < 1 || value.model_identifier.length > 128)) ||
    typeof value.model_reported_insufficient_evidence !== "boolean" ||
    !sha256(value.index_fingerprint) || !sha256(value.document_manifest_hash) || !sha256(value.run_fingerprint) ||
    !sha256(value.context_fingerprint) || typeof value.selected_build_id !== "string" || !/^[0-9a-f]{32}$/.test(value.selected_build_id) ||
    !sha256(value.selected_report_sha256) || !count(value.selection_revision) || value.selection_revision < 1 ||
    !Array.isArray(value.text_evidence) || value.text_evidence.length > 32 || !value.text_evidence.every(isAskTextEvidence) ||
    !Array.isArray(value.image_evidence) || value.image_evidence.length > 16 || !value.image_evidence.every(isAskImageEvidence) ||
    !Array.isArray(value.referenced_citations) || value.referenced_citations.length > 48 ||
    !value.referenced_citations.every((label) => typeof label === "string" && /^(?:T|I)[1-9][0-9]{0,2}$/.test(label)) ||
    new Set(value.referenced_citations).size !== value.referenced_citations.length ||
    !Array.isArray(value.warnings) || value.warnings.length > 32 ||
    !value.warnings.every((warning) => typeof warning === "string" && /^[a-z][a-z0-9_]{1,63}$/.test(warning))) return false;
  const evidenceLabels = new Set([
    ...value.text_evidence.map((item) => (item as Record<string, unknown>).label),
    ...value.image_evidence.map((item) => (item as Record<string, unknown>).label),
  ]);
  if (evidenceLabels.size !== value.text_evidence.length + value.image_evidence.length) return false;
  if (!value.referenced_citations.every((label) => evidenceLabels.has(label))) return false;
  if (value.outcome === "answered") return typeof value.answer === "string" && value.answer.length > 0 && value.failure_code === null && typeof value.model_identifier === "string" && value.model_identifier.length > 0;
  return value.answer === null && value.failure_code === "no_usable_evidence" && value.model_identifier === null &&
    value.model_reported_insufficient_evidence === false &&
    value.text_evidence.length === 0 && value.image_evidence.length === 0 && value.referenced_citations.length === 0;
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

function isChatModelCatalog(value: unknown): value is ChatModelCatalog {
  return isRecord(value) && (value.availability === "available" || value.availability === "unavailable") &&
    typeof value.configured_model === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(value.configured_model) &&
    typeof value.configured_model_available === "boolean" && Array.isArray(value.available_models) &&
    value.available_models.length <= 128 && value.available_models.every((model) => typeof model === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(model)) &&
    (value.error_code === null || value.error_code === "ollama_unavailable") &&
    (value.availability === "available"
      ? value.error_code === null && new Set(value.available_models).size === value.available_models.length &&
        value.configured_model_available === value.available_models.includes(value.configured_model)
      : value.error_code === "ollama_unavailable" && value.available_models.length === 0 && !value.configured_model_available);
}

function isGeneralChatResponse(value: unknown): value is GeneralChatResponse {
  return isRecord(value) && value.mode === "general" && typeof value.answer === "string" && value.answer.length > 0 && value.answer.length <= 16_000 &&
    typeof value.model === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(value.model);
}

const DEFAULT_LOCAL_GATEWAY = import.meta.env.VITE_RAG_LOCAL_GATEWAY === "true";

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

const canonicalVariantIds = [
  "1_Dense_only", "2_BM25_only", "3_Hybrid_static", "4_Hybrid_adaptive", "5_Hybrid_reranker", "6_Full_pipeline",
] as const;
const canonicalOverrideKeys = new Set([
  "retrieval_mode", "use_reranker", "adaptive_weighting", "use_weighted_fusion", "use_filtering",
  "use_percentile_filtering", "percentile_cutoff",
]);

function isAblationVariantCatalog(value: unknown): value is AblationVariantCatalog {
  return isRecord(value) && Object.keys(value).length === 2 && Object.hasOwn(value, "schema_version") && Object.hasOwn(value, "variants") &&
    value.schema_version === "1" && Array.isArray(value.variants) &&
    value.variants.length === canonicalVariantIds.length && value.variants.every((item: unknown, index: number) => {
      if (!isRecord(item) || Object.keys(item).length !== 2 || item.variant_id !== canonicalVariantIds[index] || !isRecord(item.overrides)) return false;
      const entries = Object.entries(item.overrides);
      return entries.length > 0 && entries.every(([key, override]) => canonicalOverrideKeys.has(key) &&
        (typeof override === "string" || typeof override === "boolean" || (typeof override === "number" && Number.isFinite(override))));
    });
}

function isStoredArtifactResult(value: unknown, artifactType: CatalogArtifactRecord["artifact_type"]): value is Omit<StoredArtifactResult, "raw_json"> {
  return isRecord(value) && Object.keys(value).length === 3 && Object.hasOwn(value, "artifact_type") &&
    Object.hasOwn(value, "serialization_schema_version") && Object.hasOwn(value, "payload") &&
    value.artifact_type === artifactType && value.serialization_schema_version === "1" && isRecord(value.payload);
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
  private readonly localGateway: boolean;

  constructor(options: { token?: string | null; baseUrl?: string; fetcher?: FetchLike; localGateway?: boolean } = {}) {
    this.token = options.token ?? null;
    this.baseUrl = options.baseUrl ?? "";
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
    this.localGateway = options.localGateway ?? DEFAULT_LOCAL_GATEWAY;
  }

  private async request(path: string, options: { method?: string; body?: unknown; protected?: boolean; signal?: AbortSignal; maxResponseBytes?: number; preserveJsonText?: boolean } = {}): Promise<unknown> {
    const headers = new Headers();
    if (options.protected !== false && path.startsWith("/api/v1/")) {
      if (!this.localGateway) {
        if (!this.token) throw new ApiError("http", "authentication_required", 401);
        headers.set("Authorization", `Bearer ${this.token}`);
      }
    }
    const init: RequestInit = {
      method: options.method ?? "GET",
      headers,
      ...(options.signal ? { signal: options.signal } : {}),
    };
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
    let payload: unknown;
    let rawJson: string | undefined;
    if (options.maxResponseBytes !== undefined) {
      if (!(response.headers.get("content-type") ?? "").toLowerCase().includes("application/json")) {
        throw new ApiError("non_json", "non_json_response", response.status);
      }
      const contentLength = response.headers.get("content-length");
      if (contentLength && /^\d+$/.test(contentLength) && Number(contentLength) > options.maxResponseBytes) {
        await response.body?.cancel();
        throw new ApiError("malformed_response", "response_body_too_large", response.status);
      }
      const reader = response.body?.getReader();
      if (!reader) malformed(response.status);
      const chunks: Uint8Array[] = [];
      let byteLength = 0;
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          byteLength += part.value.byteLength;
          if (byteLength > options.maxResponseBytes) {
            await reader.cancel();
            throw new ApiError("malformed_response", "response_body_too_large", response.status);
          }
          chunks.push(part.value);
        }
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw new ApiError("malformed_response", "malformed_response", response.status);
      }
      try {
        const joined = new Uint8Array(byteLength);
        let offset = 0;
        for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength; }
        rawJson = new TextDecoder("utf-8", { fatal: true }).decode(joined);
        payload = JSON.parse(rawJson) as unknown;
      } catch {
        throw new ApiError("malformed_response", "malformed_response", response.status);
      }
    } else {
      payload = await parseJson(response);
    }
    if (!response.ok) {
      const code = errorCode(payload);
      throw new ApiError("http", code ?? "http_error", response.status);
    }
    return options.preserveJsonText ? { payload, rawJson } : payload;
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

  async getIndexSelection(): Promise<IndexSelectionState> {
    const value = await this.request("/api/v1/index/selection");
    if (!isIndexSelectionState(value)) malformed();
    return value;
  }

  async selectIndex(input: IndexSelectionRequest): Promise<IndexSelectionState> {
    if (!/^[0-9a-f]{64}$/.test(input.expected_manifest_hash) ||
      !/^[0-9a-f]{64}$/.test(input.expected_index_fingerprint) ||
      !/^[0-9a-f]{32}$/.test(input.build_id) ||
      !Number.isSafeInteger(input.expected_revision) || input.expected_revision < 0 ||
      !/^[0-9a-f]{32}$/.test(input.operation_id)) {
      throw new ApiError("malformed_response", "selection_request_invalid");
    }
    const value = await this.request("/api/v1/index/selection", { method: "POST", body: input });
    if (!isIndexSelectionState(value) || value.selection_status !== "selected" ||
      value.selection?.build_id !== input.build_id ||
      value.selection.document_manifest_hash !== input.expected_manifest_hash ||
      value.selection.index_fingerprint !== input.expected_index_fingerprint) malformed();
    return value;
  }

  async ask(input: AskRequest, signal?: AbortSignal): Promise<AskResponse> {
    if (typeof input.question !== "string" || !input.question.trim() || input.question.length > 4000) {
      throw new ApiError("malformed_response", "question_invalid");
    }
    const value = await this.request("/api/v1/ask", {
      method: "POST", body: input, ...(signal ? { signal } : {}),
    });
    if (!isAskResponse(value)) malformed();
    return value;
  }

  async chatModels(signal?: AbortSignal): Promise<ChatModelCatalog> {
    const value = await this.request("/api/v1/chat/models", signal ? { signal } : {});
    if (!isChatModelCatalog(value)) malformed();
    return value;
  }

  async generalChat(input: GeneralChatRequest, signal?: AbortSignal): Promise<GeneralChatResponse> {
    if (!input || typeof input.question !== "string" || !input.question.trim() || input.question.length > 4000 ||
      !Array.isArray(input.history) || input.history.length > 8 || input.history.some((turn) => !turn ||
        typeof turn.user !== "string" || typeof turn.assistant !== "string") ||
      (input.model !== undefined && (typeof input.model !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/.test(input.model))) ||
      (input.generation !== undefined && (!input.generation ||
        typeof input.generation.temperature !== "number" || !Number.isFinite(input.generation.temperature) ||
        input.generation.temperature < 0 || input.generation.temperature > 2 ||
        typeof input.generation.max_output_tokens !== "number" || !Number.isInteger(input.generation.max_output_tokens) ||
        input.generation.max_output_tokens < 32 || input.generation.max_output_tokens > 2048))) {
      throw new ApiError("malformed_response", "chat_request_invalid");
    }
    const value = await this.request("/api/v1/chat", {
      method: "POST", body: input, ...(signal ? { signal } : {}),
    });
    if (!isGeneralChatResponse(value)) malformed();
    return value;
  }

  async getAskImage(reference: string, signal?: AbortSignal): Promise<Blob> {
    if (!this.localGateway && !this.token) throw new ApiError("http", "authentication_required", 401);
    if (!/^[0-9a-f]{32}$/.test(reference)) throw new ApiError("malformed_response", "image_reference_invalid");
    let response: Response;
    try {
      const headers = new Headers();
      if (!this.localGateway) headers.set("Authorization", `Bearer ${this.token}`);
      response = await this.fetcher(`/api/v1/ask/images/${reference}`, {
        method: "GET",
        headers,
        ...(signal ? { signal } : {}),
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError("network", "network_error");
    }
    if (!response.ok) {
      let code = "request_failed";
      try { code = errorCode(await parseJson(response)) ?? code; } catch { /* retain a stable fallback */ }
      throw new ApiError("http", code, response.status);
    }
    const mediaType = (response.headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase() ?? "";
    if (!["image/png", "image/jpeg", "image/webp"].includes(mediaType)) malformed(response.status);
    const blob = await response.blob();
    if (blob.size <= 0 || blob.size > 8 * 1024 * 1024) malformed(response.status);
    return blob;
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

  async listCanonicalAblationVariants(): Promise<AblationVariantCatalog> {
    const value = await this.request("/api/v1/ablations/variants");
    if (!isAblationVariantCatalog(value)) malformed();
    return value;
  }

  async loadArtifactResult(artifactId: string, artifactType: CatalogArtifactRecord["artifact_type"], plan?: AblationPlanQuery): Promise<StoredArtifactResult> {
    const params = new URLSearchParams();
    if (plan) {
      params.set("base_config", JSON.stringify(plan.base_config));
      params.set("document_manifest_hash", plan.document_manifest_hash);
    }
    const value = await this.request(appendQuery(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/result`, params), {
      maxResponseBytes: 8 * 1024 * 1024, preserveJsonText: true,
    });
    if (!isRecord(value) || !isStoredArtifactResult(value.payload, artifactType) || typeof value.rawJson !== "string") malformed();
    return { ...value.payload, raw_json: value.rawJson };
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

const documentRecordKeys = ["document_id", "relative_path", "size_bytes", "sha256"] as const;
const documentsResponseKeys = [
  "inventory_status", "documents", "manifest_hash", "total", "limit", "offset", "complete", "max_upload_bytes", "max_pages",
] as const;
const documentImportKeys = ["outcome", "document", "manifest_hash", "max_upload_bytes", "error_code"] as const;

function isBootstrapDocument(value: unknown): value is BootstrapDocumentRecord {
  if (!isRecord(value) || !hasExactKeys(value, documentRecordKeys) ||
    typeof value.document_id !== "string" || !/^doc_[a-f0-9]{64}$/.test(value.document_id) ||
    typeof value.relative_path !== "string" || value.relative_path.length < 1 || value.relative_path.length > 2048 ||
    value.relative_path.startsWith("/") || value.relative_path.includes("\\") ||
    value.relative_path.split("/").some((part) => part === "" || part === "." || part === "..") ||
    /^[A-Za-z]:/.test(value.relative_path) || !/\.pdf$/i.test(value.relative_path) ||
    !count(value.size_bytes) || !sha256(value.sha256)) return false;
  return true;
}

function isBootstrapDocuments(value: unknown, limit: number, offset: number): value is BootstrapDocumentsResponse {
  return isRecord(value) && hasExactKeys(value, documentsResponseKeys) &&
    value.inventory_status === "available" && Array.isArray(value.documents) && value.documents.length <= limit &&
    value.documents.every(isBootstrapDocument) && sha256(value.manifest_hash) && count(value.total) &&
    value.limit === limit && value.offset === offset && value.complete === true &&
    value.documents.length === Math.min(limit, Math.max(0, value.total - offset)) &&
    Number.isSafeInteger(value.max_upload_bytes) && Number(value.max_upload_bytes) >= 1 && Number(value.max_upload_bytes) <= 16 * 1024 * 1024 &&
    value.max_pages === 200;
}

function isBootstrapDocumentImport(value: unknown): value is BootstrapDocumentImportResponse {
  if (!isRecord(value) || !hasExactKeys(value, documentImportKeys) || !isBootstrapDocument(value.document) ||
    !Number.isSafeInteger(value.max_upload_bytes) || Number(value.max_upload_bytes) < 1 || Number(value.max_upload_bytes) > 16 * 1024 * 1024 ||
    !(value.manifest_hash === null || sha256(value.manifest_hash)) ||
    !(value.error_code === null || (typeof value.error_code === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(value.error_code)))) return false;
  return value.outcome === "imported"
    ? sha256(value.manifest_hash) && value.error_code === null
    : value.outcome === "committed_inventory_unavailable" && value.manifest_hash === null && value.error_code !== null;
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
  private readonly localGateway: boolean;

  constructor(options: { token?: string | null; fetcher?: FetchLike; prefix?: string; localGateway?: boolean } = {}) {
    this.token = options.token ?? null;
    this.fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
    this.prefix = options.prefix ?? "/bootstrap-api";
    this.localGateway = options.localGateway ?? DEFAULT_LOCAL_GATEWAY;
  }

  private async request(path: string, options: {
    method?: string; body?: unknown; protected?: boolean; binaryBody?: Blob; filename?: string;
    signal?: AbortSignal; acceptStatuses?: readonly number[];
  } = {}): Promise<unknown> {
    const headers = new Headers();
    if (options.protected !== false) {
      if (!this.localGateway) {
        if (!this.token) throw new ApiError("http", "authentication_required", 401);
        headers.set("Authorization", `Bearer ${this.token}`);
      }
    }
    const init: RequestInit = { method: options.method ?? "GET", headers };
    if (options.signal) init.signal = options.signal;
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    } else if (options.binaryBody !== undefined) {
      headers.set("Content-Type", "application/pdf");
      headers.set("X-Document-Filename", options.filename ?? "");
      init.body = options.binaryBody;
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
    if (!response.ok && !options.acceptStatuses?.includes(response.status)) {
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

  async listDocuments(options: { limit?: number; offset?: number; signal?: AbortSignal } = {}): Promise<BootstrapDocumentsResponse> {
    const limit = options.limit ?? 50;
    const offset = options.offset ?? 0;
    if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 1_000_000) {
      throw new ApiError("malformed_response", "pagination_invalid");
    }
    const value = await this.request(
      `/v1/bootstrap/documents?limit=${limit}&offset=${offset}`,
      options.signal ? { signal: options.signal } : {},
    );
    if (!isBootstrapDocuments(value, limit, offset)) malformed();
    return value;
  }

  async uploadDocument(file: File, options: { signal?: AbortSignal } = {}): Promise<BootstrapDocumentImportResponse> {
    if (!file || !/^[A-Za-z0-9][A-Za-z0-9 _().-]{0,114}\.pdf$/.test(file.name) || file.size < 1) {
      throw new ApiError("malformed_response", "document_file_invalid");
    }
    const value = await this.request("/v1/bootstrap/documents", {
      method: "POST", binaryBody: file, filename: file.name, acceptStatuses: [503],
      ...(options.signal ? { signal: options.signal } : {}),
    });
    if (!isBootstrapDocumentImport(value)) malformed();
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
