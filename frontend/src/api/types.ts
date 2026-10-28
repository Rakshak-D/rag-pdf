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
