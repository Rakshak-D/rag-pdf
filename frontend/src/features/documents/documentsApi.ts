import { ApiError } from "../../api/client";
import { validateResolvedConfig } from "../index-builds/configValidation";

const MAX_CONFIGURATION_BYTES = 256 * 1024;
const LOCAL_GATEWAY = import.meta.env.VITE_RAG_LOCAL_GATEWAY === "true";

export interface TrustedBootstrapConfiguration {
  schema_version: "1";
  configuration_source: "trusted_server_defaults";
  resolved_config: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Fetch a complete configuration from the authenticated bootstrap boundary.
 * In local-gateway mode the gateway injects its server-held credential. */
export async function getTrustedBootstrapConfiguration(
  token: string | null,
  signal?: AbortSignal,
): Promise<TrustedBootstrapConfiguration> {
  if (!LOCAL_GATEWAY && !token) throw new ApiError("http", "authentication_required", 401);
  const headers = new Headers({ Accept: "application/json" });
  if (!LOCAL_GATEWAY && token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch("/bootstrap-api/v1/bootstrap/configuration", {
      method: "GET", headers, cache: "no-store", ...(signal ? { signal } : {}),
    });
  } catch {
    throw new ApiError("network", "bootstrap_proxy_unavailable");
  }
  const contentType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== "application/json") throw new ApiError("non_json", "bootstrap_proxy_unavailable", response.status);
  const lengthHeader = response.headers.get("content-length");
  if (lengthHeader && (!/^\d+$/.test(lengthHeader) || Number(lengthHeader) > MAX_CONFIGURATION_BYTES)) {
    throw new ApiError("malformed_response", "configuration_response_invalid", response.status);
  }
  let text: string;
  try { text = await response.text(); }
  catch { throw new ApiError("network", "bootstrap_proxy_unavailable", response.status); }
  if (new TextEncoder().encode(text).byteLength > MAX_CONFIGURATION_BYTES) {
    throw new ApiError("malformed_response", "configuration_response_invalid", response.status);
  }
  let payload: unknown;
  try { payload = JSON.parse(text) as unknown; }
  catch { throw new ApiError("malformed_response", "configuration_response_invalid", response.status); }
  if (!response.ok) {
    const code = isRecord(payload) && isRecord(payload.error) && typeof payload.error.code === "string" &&
      /^[a-z][a-z0-9_]{1,63}$/.test(payload.error.code) ? payload.error.code : "bootstrap_configuration_unavailable";
    throw new ApiError("http", code, response.status);
  }
  if (!isRecord(payload) || Object.keys(payload).sort().join(",") !== "configuration_source,resolved_config,schema_version" ||
    payload.schema_version !== "1" || payload.configuration_source !== "trusted_server_defaults" || !isRecord(payload.resolved_config)) {
    throw new ApiError("malformed_response", "configuration_response_invalid", response.status);
  }
  const validation = validateResolvedConfig(JSON.stringify(payload.resolved_config));
  if (!validation.value) throw new ApiError("malformed_response", "configuration_response_invalid", response.status);
  return {
    schema_version: "1",
    configuration_source: "trusted_server_defaults",
    resolved_config: validation.value,
  };
}
