import { ApiError } from "../api/client";

export function apiErrorText(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local API request failed.";
  if (error.code === "bootstrap_proxy_unconfigured") return "The dedicated bootstrap proxy is not configured. Set RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET to the loopback bootstrap service and restart Vite.";
  if (error.code === "bootstrap_proxy_unavailable") return "Could not reach the bootstrap service through its separate proxy. Confirm the bootstrap server and RAG_WORKBENCH_BOOTSTRAP_DEV_PROXY_TARGET.";
  if (error.code === "index_already_exists" || error.code === "fresh_index_exists") return "An index directory already exists for this fingerprint. Fresh-only submission will not reuse or repair it.";
  if (error.code === "manifest_changed" || error.code === "index_fingerprint_changed" || error.code === "confirmation_changed") return "The source or configuration identity changed since preview. Preview again and confirm the new identities.";
  if (error.code === "fingerprint_already_active") return "A build for this index fingerprint is already queued or running. Inspect job history.";
  if (error.code === "queue_capacity_exhausted") return "The bootstrap queue is full. Inspect existing jobs and submit later if appropriate.";
  if (error.code === "manager_not_accepting" || error.code === "bootstrap_service_unavailable" || error.code === "job_store_unavailable") return "The bootstrap service is not accepting requests or its job store is unavailable. Inspect job history before any new submission.";
  if (error.code === "job_admission_failed") return "The job admission result could not be confirmed. Inspect job history for the retained request ID before deciding what to do next.";
  if (error.code === "request_id_conflict") return "This client request ID already belongs to a different build identity. Inspect job history and do not reuse the ID.";
  if (error.code === "configuration_source_mismatch") return "The configuration PDF source does not match the trusted source configured by the bootstrap service.";
  if (error.code === "manifest_empty") return "No PDFs were found in the trusted source directory.";
  if (error.status === 401 || error.code === "authentication_required") return "Authentication failed. Check the local bearer token.";
  if (error.kind === "network") return "Could not reach the local API. Confirm that the backend is running.";
  if (error.kind === "non_json" || error.kind === "malformed_response") return "The backend returned an unexpected response.";
  if (error.code === "manifest_empty") return "No PDF documents were found. Inspect documents again after adding PDFs.";
  if (error.code === "manifest_changed") return "Source documents changed. Inspect documents again before checking the index.";
  if (error.code === "index_readiness_unconfigured") return "The configured index report check is unavailable in this application.";
  if (error.code === "manifest_source_changed" || error.code === "manifest_invalid") return "The document source could not be inspected safely. Check the source and inspect again.";
  if (error.code === "manifest_source_unavailable" || error.code === "manifest_source_unconfigured") return "The configured document source is unavailable. Check the local runtime configuration.";
  if (error.code === "configuration_invalid" || error.code === "destructive_configuration_forbidden") return "The backend rejected the configuration. Check the complete configuration and safety settings.";
  if (error.code === "request_body_too_large" || error.code === "response_body_too_large" || error.code === "manifest_response_too_large") return "The request or response exceeds the configured API size limit.";
  if (error.code === "job_execution_unconfigured") return "The backend job manager is not configured.";
  if (error.code === "job_not_cancellable") return "This job is no longer queued and cannot be cancelled.";
  if (error.code === "ablation_plan_required" || error.code === "ablation_plan_invalid") return "Enter the original compatible configuration as a JSON object.";
  if (error.code === "ablation_plan_mismatch") return "That configuration does not match this artifact's accepted ablation plan.";
  return `The API request failed (${error.code}).`;
}
