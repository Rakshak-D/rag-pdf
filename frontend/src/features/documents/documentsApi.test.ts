import { afterEach, describe, expect, it, vi } from "vitest";
import { REQUIRED_RESOLVED_CONFIG_FIELDS } from "../index-builds/configValidation";
import { getTrustedBootstrapConfiguration } from "./documentsApi";

function resolvedConfig(): Record<string, unknown> {
  const value: Record<string, unknown> = Object.fromEntries(REQUIRED_RESOLVED_CONFIG_FIELDS.map((field) => [field, null]));
  value.pdf_dir = "trusted-source";
  value.reset_collections_on_start = false;
  value.workbench_policy = { allow_destructive_index_reset: false, allow_collection_delete: false };
  return value;
}

afterEach(() => vi.unstubAllGlobals());

describe("trusted bootstrap configuration", () => {
  it("uses the authenticated read-only endpoint and validates the complete resolved schema", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: resolvedConfig(),
    }));
    vi.stubGlobal("fetch", fetcher);

    const result = await getTrustedBootstrapConfiguration("bootstrap-session-token");

    expect(result.configuration_source).toBe("trusted_server_defaults");
    expect(result.resolved_config.pdf_dir).toBe("trusted-source");
    expect(fetcher).toHaveBeenCalledWith("/bootstrap-api/v1/bootstrap/configuration", expect.objectContaining({
      method: "GET", cache: "no-store",
    }));
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer bootstrap-session-token");
  });

  it("rejects destructive or malformed server configuration without exposing response content", async () => {
    const destructive = resolvedConfig();
    destructive.reset_collections_on_start = true;
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({
      schema_version: "1", configuration_source: "trusted_server_defaults", resolved_config: destructive,
    }));
    vi.stubGlobal("fetch", fetcher);

    await expect(getTrustedBootstrapConfiguration("bootstrap-session-token")).rejects.toMatchObject({
      kind: "malformed_response", code: "configuration_response_invalid",
    });
  });

  it("rejects oversized configuration bodies before parsing", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(" ".repeat(257 * 1024), {
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetcher);
    await expect(getTrustedBootstrapConfiguration("bootstrap-session-token")).rejects.toMatchObject({
      kind: "malformed_response", code: "configuration_response_invalid",
    });
  });
});
