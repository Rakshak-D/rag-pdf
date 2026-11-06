export type ApiVersion = "v1";

export interface HealthResponse {
  status: "ok";
  api_version: ApiVersion;
}

export type ModelComponentKind = "text" | "image" | "reranker";
export type ModelState = "unloaded" | "loading" | "ready" | "failed";

export interface ModelComponentStatus {
  component_kind: ModelComponentKind;
  model_identifier: string;
  state: ModelState;
  error_code: string | null;
  explicitly_warmed: boolean;
}

export interface RuntimeModelsResponse {
  components: ModelComponentStatus[];
}

export interface BootstrapStatusResponse {
  mode: "bootstrap";
  status: "running";
  accepting_jobs: boolean;
  worker_count: 1;
  queue_capacity: number;
}

export interface BootstrapDocumentRecord {
  document_id: string;
  relative_path: string;
  size_bytes: number;
  sha256: string;
}

export interface BootstrapDocumentsResponse {
  inventory_status: "available";
  documents: BootstrapDocumentRecord[];
  manifest_hash: string;
  total: number;
  limit: number;
  offset: number;
  complete: true;
  max_upload_bytes: number;
  max_pages: 200;
}

export interface BootstrapDocumentImportResponse {
  outcome: "imported" | "committed_inventory_unavailable";
  document: BootstrapDocumentRecord;
  manifest_hash: string | null;
  max_upload_bytes: number;
  error_code: string | null;
}

export interface BootstrapPreviewRequest {
  resolved_config: Record<string, unknown>;
}

export interface BootstrapPreviewResponse {
  document_count: number;
  manifest_hash: string;
  index_fingerprint: string;
  run_fingerprint: string;
  existing_index: boolean;
  fresh_build_eligible: boolean;
  reason_code: string;
}

export interface BootstrapInventoryRequest {
  resolved_config: Record<string, unknown>;
  expected_manifest_hash: string;
  limit?: number;
  offset?: number;
}

export type BootstrapInventoryCompatibility =
  | "report_match"
  | "mismatch"
  | "failed"
  | "incomplete"
  | "invalid"
  | "unavailable";

export interface BootstrapInventoryCandidate {
  candidate_id: string;
  build_id: string | null;
  report_reference: string | null;
  compatibility: BootstrapInventoryCompatibility;
  reason_code: string;
  expected_index_fingerprint: string;
  document_manifest_hash: string;
  reported_index_fingerprint: string | null;
  reported_document_manifest_hash: string | null;
  report_status: "completed" | "completed_with_warnings" | "failed" | null;
  reported_text_records: number | null;
  reported_image_records: number | null;
  check_level: "report";
  store_checked: false;
}

export interface BootstrapInventoryPage {
  expected_index_fingerprint: string;
  document_manifest_hash: string;
  candidates: BootstrapInventoryCandidate[];
  total_candidates: number | null;
  offset: number;
  limit: number;
  complete: boolean;
  reason_code: string;
  check_level: "report";
  store_checked: false;
}

export interface BootstrapJobRequest {
  resolved_config: Record<string, unknown>;
  expected_manifest_hash: string;
  expected_index_fingerprint: string;
  client_request_id: string;
}

export type BootstrapJobStatus = "queued" | "running" | "succeeded" | "failed" | "interrupted";

export interface BootstrapJobRecord {
  job_id: string;
  client_request_id: string;
  status: BootstrapJobStatus;
  manifest_hash: string;
  index_fingerprint: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  build_id: string | null;
  report_reference: string | null;
  text_records: number | null;
  image_records: number | null;
  partial_persistence: boolean | null;
}

export interface BootstrapJobEnvelope {
  job: BootstrapJobRecord;
}

export interface BootstrapJobsResponse {
  jobs: BootstrapJobRecord[];
  limit: number;
  offset: number;
}

export type JobType = "evaluation" | "ablation";
export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "cancelled"
  | "interrupted";
export type ResultStatus = "succeeded" | "partial" | "failed";

export interface ExperimentJobRecord {
  job_id: string;
  job_type: JobType;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  benchmark_id: string;
  benchmark_version: string;
  run_fingerprint: string | null;
  shared_index_fingerprint: string | null;
  document_manifest_hash: string | null;
  artifact_id: string | null;
  error_code: string | null;
  result_status: ResultStatus | null;
}

export type ArtifactType = "evaluation_result" | "ablation_result";
export type ArtifactStatus = ResultStatus;

export interface CatalogArtifactRecord {
  artifact_id: string;
  artifact_type: ArtifactType;
  relative_path: string;
  byte_length: number;
  serialization_schema_version: string;
  benchmark_id: string;
  benchmark_version: string;
  status: ArtifactStatus;
  run_fingerprint: string | null;
  evaluation_fingerprint: string | null;
  document_manifest_hash: string | null;
  shared_index_fingerprint: string | null;
  case_count: number | null;
  succeeded_case_count: number | null;
  failed_case_count: number | null;
}

export type AggregateStatus = "computed" | "partial" | "no_eligible_values";
export type OptimizationDirection = "maximize" | "minimize" | "neutral";

export interface CatalogMetricRecord {
  artifact_id: string;
  variant_id: string;
  metric_id: string;
  metric_name: string;
  status: AggregateStatus;
  value: number | null;
  unit: string;
  optimization_direction: OptimizationDirection;
  reference_kind: string;
  implementation_version: string;
  eligible_count: number;
  skipped_count: number;
  failed_count: number;
  unavailable_count: number;
  model_identifier: string | null;
  methodology_note: string | null;
}

export type ComparisonState = "comparable" | "incomplete" | "not_comparable";

export interface CatalogComparisonRecord {
  artifact_id: string;
  metric_id: string;
  state: ComparisonState;
  reason_code: string | null;
}

export interface ApiErrorEnvelope {
  error: { code: string };
}

export interface RunPlanInput {
  plan_schema_version: string;
  document_manifest_hash: string;
  resolved_config_json: string;
  configuration_snapshot_hash: string;
  index_fingerprint: string;
  run_fingerprint: string;
}

export interface DocumentManifestEntry {
  relative_path: string;
  size_bytes: number;
  sha256: string;
  document_id: string;
}

export interface DocumentManifest {
  schema_version: string;
  content_hash: string;
  entry_count: number;
  empty: boolean;
  entries: DocumentManifestEntry[];
}

export interface RunPlanPreparationRequest {
  resolved_config: Record<string, unknown>;
  expected_manifest_hash: string;
}

export interface RunPlanPreparationResponse {
  run_plan: RunPlanInput;
  manifest: { schema_version: string; content_hash: string; entry_count: number };
}

export interface IndexReadinessRequest {
  workflow: "evaluation" | "ablation";
  resolved_config: Record<string, unknown>;
  expected_manifest_hash: string;
}

export interface IndexReadinessResponse {
  status: "report_match" | "mismatch" | "unavailable" | "indeterminate";
  reason_code: string;
  workflow: "evaluation" | "ablation";
  document_manifest_hash: string;
  expected_index_fingerprint: string;
  build_id: string | null;
  report_status: string | null;
  reported_text_records: number | null;
  reported_image_records: number | null;
  observed_text_records: null;
  observed_image_records: null;
  store_checked: false;
  check_level: "report";
}

export type IndexSelectionStatus = "unselected" | "selected" | "stale" | "invalid" | "legacy_configured";

export interface IndexSelectionIdentity {
  build_id: string;
  report_reference: string;
  document_manifest_hash: string;
  index_fingerprint: string;
  report_sha256: string;
  configuration_snapshot_hash: string;
  run_fingerprint: string;
  report_status: "completed" | "completed_with_warnings";
  selection_revision: number;
}

export interface IndexSelectionState {
  selection_status: IndexSelectionStatus;
  revision: number | null;
  selection: IndexSelectionIdentity | null;
  legacy_report_reference: string | null;
  reason_code: string;
  check_level: "report";
  store_checked: false;
}

export interface IndexSelectionRequest {
  resolved_config: Record<string, unknown>;
  expected_manifest_hash: string;
  expected_index_fingerprint: string;
  build_id: string;
  expected_revision: number;
  operation_id: string;
}

export interface AskTextEvidence {
  label: string;
  document_id: string;
  source_path: string;
  page_number: number | null;
  passage: string;
  record_id: string | null;
  truncated: boolean;
}

export interface AskImageEvidence {
  label: string;
  document_id: string;
  source_path: string;
  page_number: number | null;
  caption: string;
  record_id: string | null;
  image_reference: string | null;
  content_type: "image/png" | "image/jpeg" | "image/webp" | null;
  size_bytes: number | null;
}

export interface AskRequest {
  question: string;
}

export interface ChatTurn {
  user: string;
  assistant: string;
}

export interface GeneralChatGenerationOptions {
  temperature: number;
  max_output_tokens: number;
}

export interface GeneralChatRequest {
  question: string;
  history: ChatTurn[];
  model?: string;
  generation?: GeneralChatGenerationOptions;
}

export interface GeneralChatResponse {
  mode: "general";
  answer: string;
  model: string;
}

export interface ChatModelCatalog {
  availability: "available" | "unavailable";
  configured_model: string;
  configured_model_available: boolean;
  available_models: string[];
  error_code: "ollama_unavailable" | null;
}

export interface AskResponse {
  query_id: string;
  question: string;
  outcome: "answered" | "insufficient_evidence";
  answer: string | null;
  model_reported_insufficient_evidence: boolean;
  failure_code: string | null;
  model_identifier: string | null;
  index_fingerprint: string;
  document_manifest_hash: string;
  run_fingerprint: string;
  context_fingerprint: string;
  selected_build_id: string;
  selected_report_sha256: string;
  selection_revision: number;
  text_evidence: AskTextEvidence[];
  image_evidence: AskImageEvidence[];
  referenced_citations: string[];
  warnings: string[];
}

export interface BenchmarkCaseInput {
  case_id: string;
  query: string;
  modalities?: Array<"text" | "image">;
  text_k?: number | null;
  image_k?: number | null;
  required_pages?: Array<string | number>;
  reference_text?: string | null;
}

export interface BenchmarkSuiteInput {
  benchmark_id: string;
  benchmark_version: string;
  cases: BenchmarkCaseInput[];
}

export interface EvaluationRequest {
  run_plan: RunPlanInput;
  benchmark_suite: BenchmarkSuiteInput;
}

export interface AblationRequest {
  base_config: Record<string, unknown>;
  document_manifest_hash: string;
  benchmark_suite: BenchmarkSuiteInput;
}

export interface AblationVariantDefinition {
  variant_id: string;
  overrides: Record<string, string | number | boolean>;
}

export interface AblationVariantCatalog {
  schema_version: "1";
  variants: AblationVariantDefinition[];
}

export interface StoredArtifactResult {
  artifact_type: ArtifactType;
  serialization_schema_version: "1";
  payload: Record<string, unknown>;
  raw_json: string;
}

export interface EvaluationSubmissionResponse {
  artifact: CatalogArtifactRecord;
  result: {
    benchmark_id: string;
    benchmark_version: string;
    status: ArtifactStatus;
    run_fingerprint: string;
    evaluation_fingerprint: string;
  };
}

export interface AblationSubmissionResponse {
  artifact: CatalogArtifactRecord;
  result: {
    status: ArtifactStatus;
    benchmark_id: string;
    benchmark_version: string;
    document_manifest_hash: string;
    shared_index_fingerprint: string;
    variants: Array<{
      variant_id: string;
      status: ArtifactStatus;
      error_code: string | null;
    }>;
    comparisons: Array<{
      metric_id: string;
      state: ComparisonState;
      reason_code: string | null;
    }>;
  };
}

export interface JobEnvelope {
  job: ExperimentJobRecord;
}

export interface JobsResponse {
  jobs: ExperimentJobRecord[];
}

export interface ArtifactsResponse {
  artifacts: CatalogArtifactRecord[];
}

export interface MetricsResponse {
  metrics: CatalogMetricRecord[];
}

export interface ComparisonsResponse {
  comparisons: CatalogComparisonRecord[];
}

export interface ArtifactListFilters {
  artifact_type?: ArtifactType;
  benchmark_id?: string;
  benchmark_version?: string;
  run_fingerprint?: string;
  index_fingerprint?: string;
  status?: ArtifactStatus;
  limit?: number;
  offset?: number;
}

export interface JobListFilters {
  job_type?: JobType;
  status?: JobStatus;
  limit?: number;
  offset?: number;
}

export interface AblationPlanQuery {
  base_config: Record<string, unknown>;
  document_manifest_hash: string;
}
