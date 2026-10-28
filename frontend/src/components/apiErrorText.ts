import { ApiError } from "../api/client";

export function apiErrorText(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local API request failed.";
  if (error.status === 401 || error.code === "authentication_required") return "Authentication failed. Check the local bearer token.";
  if (error.kind === "network") return "Could not reach the local API. Confirm that the backend is running.";
  if (error.kind === "non_json" || error.kind === "malformed_response") return "The backend returned an unexpected response.";
  if (error.code === "manifest_empty") return "No PDF documents were found. Inspect documents again after adding PDFs.";
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
