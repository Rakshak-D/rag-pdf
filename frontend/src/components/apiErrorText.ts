import { ApiError } from "../api/client";

export function apiErrorText(error: unknown): string {
  if (!(error instanceof ApiError)) return "The local API request failed.";
  if (error.status === 401 || error.code === "authentication_required") return "Authentication failed. Check the local bearer token.";
  if (error.kind === "network") return "Could not reach the local API. Confirm that the backend is running.";
  if (error.kind === "non_json" || error.kind === "malformed_response") return "The backend returned an unexpected response.";
  if (error.code === "job_execution_unconfigured") return "The backend job manager is not configured.";
  if (error.code === "job_not_cancellable") return "This job is no longer queued and cannot be cancelled.";
  if (error.code === "ablation_plan_required" || error.code === "ablation_plan_invalid") return "Enter the original compatible configuration as a JSON object.";
  if (error.code === "ablation_plan_mismatch") return "That configuration does not match this artifact's accepted ablation plan.";
  return `The API request failed (${error.code}).`;
}
