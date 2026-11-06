import { describe, expect, it } from "vitest";
import { apiPolicyViolations } from "../../e2e/request-policy";

describe("synthetic browser API request policy", () => {
  it("allows the invalid-token bootstrap status GET so the real API can reject it", () => {
    expect(apiPolicyViolations("/bootstrap-api/v1/bootstrap/status", "GET", "invalid")).toEqual([]);
  });

  it.each([
    ["/bootstrap-api/v1/bootstrap/status", "GET", "ordinary", "bootstrap_auth_scope_violation"],
    ["/bootstrap-api/v1/bootstrap/status", "GET", "other", "bootstrap_auth_scope_violation"],
    ["/bootstrap-api/v1/bootstrap/preview", "GET", "invalid", "bootstrap_auth_scope_violation"],
    ["/api/v1/jobs", "GET", "bootstrap", "ordinary_auth_scope_violation"],
    ["/bootstrap-api/v1/bootstrap/jobs", "POST", "bootstrap", "mutation_blocked"],
    ["/api/v1/jobs/evaluations", "POST", "ordinary", "mutation_blocked"],
  ] as const)("rejects role or mutation violation for %s", (path, method, role, violation) => {
    expect(apiPolicyViolations(path, method, role)).toContain(violation);
  });

  it("allows only the existing read-only preview and inventory POSTs", () => {
    expect(apiPolicyViolations("/bootstrap-api/v1/bootstrap/preview", "POST", "bootstrap")).toEqual([]);
    expect(apiPolicyViolations("/bootstrap-api/v1/bootstrap/index-inventory", "POST", "bootstrap")).toEqual([]);
  });
});
