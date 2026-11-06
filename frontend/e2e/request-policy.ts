export type AuthRole = "none" | "ordinary" | "bootstrap" | "invalid" | "other";

export type ApiPolicyViolation =
  | "mutation_blocked"
  | "bootstrap_auth_scope_violation"
  | "ordinary_auth_scope_violation";

const allowedPosts = new Set([
  "/bootstrap-api/v1/bootstrap/preview",
  "/bootstrap-api/v1/bootstrap/index-inventory",
]);

/** Auth and mutation policy used by the synthetic browser route guard. */
export function apiPolicyViolations(path: string, method: string, role: AuthRole): ApiPolicyViolation[] {
  const normalizedMethod = method.toUpperCase();
  const violations: ApiPolicyViolation[] = [];
  if (!new Set(["GET", "HEAD", "OPTIONS"]).has(normalizedMethod) &&
      !(normalizedMethod === "POST" && allowedPosts.has(path))) {
    violations.push("mutation_blocked");
  }
  if (path.startsWith("/bootstrap-api/v1/") &&
      (role === "ordinary" || role === "other" || role === "none" ||
        (role === "invalid" && path !== "/bootstrap-api/v1/bootstrap/status"))) {
    violations.push("bootstrap_auth_scope_violation");
  }
  if (path.startsWith("/api/v1/") &&
      (role === "bootstrap" || role === "other" || role === "none" ||
        (role === "invalid" && path !== "/api/v1/runtime/models"))) {
    violations.push("ordinary_auth_scope_violation");
  }
  return violations;
}
