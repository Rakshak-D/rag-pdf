import fs from "node:fs";
import { expect, test, type Page, type Request } from "@playwright/test";

type AuthRole = "none" | "ordinary" | "bootstrap" | "invalid" | "other";

interface ApiObservation {
  method: string;
  path: string;
  auth_role: AuthRole;
  status: number | null;
  failed?: boolean;
  response_summary?: Record<string, string | number | boolean | null>;
}

interface SafetyRecorder {
  requests: ApiObservation[];
  violations: string[];
  checks: string[];
  flush: () => Promise<void>;
}

interface ExpectedFixture {
  document_count: number;
  manifest_hash: string;
  index_fingerprint: string;
  run_fingerprint: string;
  resolved_config: Record<string, unknown>;
}

const ordinaryToken = requiredEnv("E2E_ORDINARY_TOKEN");
const bootstrapToken = requiredEnv("E2E_BOOTSTRAP_TOKEN");
const invalidToken = requiredEnv("E2E_INVALID_TOKEN");
const expected = JSON.parse(requiredEnv("E2E_EXPECTED_JSON")) as ExpectedFixture;
const evidenceFile = requiredEnv("E2E_OBSERVATIONS_FILE");
const tokens = [ordinaryToken, bootstrapToken, invalidToken];
const activeRecorders = new WeakMap<Page, SafetyRecorder>();
let browserVersion = "unknown";

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`missing_test_environment:${name}`);
  return value;
}

function tokenRole(authorization: string | null): AuthRole {
  if (!authorization) return "none";
  const match = /^Bearer\s+(.+)$/i.exec(authorization);
  if (!match) return "other";
  if (match[1] === ordinaryToken) return "ordinary";
  if (match[1] === bootstrapToken) return "bootstrap";
  if (match[1] === invalidToken) return "invalid";
  return "other";
}

function responseSummary(path: string, value: unknown): Record<string, string | number | boolean | null> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const body = value as Record<string, unknown>;
  if (path === "/api/v1/runtime/models" && Array.isArray(body.components)) {
    return { component_count: body.components.length };
  }
  if (path === "/api/v1/jobs" && Array.isArray(body.jobs)) {
    return { item_count: body.jobs.length };
  }
  if (path === "/api/v1/artifacts" && Array.isArray(body.artifacts)) {
    return { item_count: body.artifacts.length };
  }
  if (path === "/bootstrap-api/v1/bootstrap/status") {
    return {
      mode: typeof body.mode === "string" ? body.mode : null,
      accepting_jobs: typeof body.accepting_jobs === "boolean" ? body.accepting_jobs : null,
    };
  }
  if (path === "/bootstrap-api/v1/bootstrap/preview") {
    return {
      document_count: typeof body.document_count === "number" ? body.document_count : null,
      manifest_hash: typeof body.manifest_hash === "string" ? body.manifest_hash : null,
      index_fingerprint: typeof body.index_fingerprint === "string" ? body.index_fingerprint : null,
      run_fingerprint: typeof body.run_fingerprint === "string" ? body.run_fingerprint : null,
      fresh_build_eligible: typeof body.fresh_build_eligible === "boolean" ? body.fresh_build_eligible : null,
    };
  }
  if (path === "/bootstrap-api/v1/bootstrap/index-inventory") {
    return {
      candidate_count: Array.isArray(body.candidates) ? body.candidates.length : null,
      total_candidates: typeof body.total_candidates === "number" ? body.total_candidates : null,
      complete: typeof body.complete === "boolean" ? body.complete : null,
      store_checked: typeof body.store_checked === "boolean" ? body.store_checked : null,
      check_level: typeof body.check_level === "string" ? body.check_level : null,
    };
  }
  if (path === "/bootstrap-api/v1/bootstrap/jobs" && Array.isArray(body.jobs)) {
    return { item_count: body.jobs.length };
  }
  return undefined;
}

function isLoopback(hostname: string): boolean {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return normalized === "localhost" || normalized === "::1" || /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

async function attachSafetyGuards(page: Page): Promise<SafetyRecorder> {
  const base = new URL(requiredEnv("E2E_BASE_URL"));
  const requests: ApiObservation[] = [];
  const violations = new Set<string>();
  const checks: string[] = [];
  const byRequest = new WeakMap<Request, ApiObservation>();
  const pending: Promise<void>[] = [];
  const allowedPosts = new Set([
    "/bootstrap-api/v1/bootstrap/preview",
    "/bootstrap-api/v1/bootstrap/index-inventory",
  ]);

  page.on("request", (request) => {
    try {
      const url = new URL(request.url());
      if (["http:", "https:", "ws:", "wss:"].includes(url.protocol) && !isLoopback(url.hostname)) {
        violations.add("non_loopback_request");
      }
      if (["http:", "https:", "ws:", "wss:"].includes(url.protocol) && url.host !== base.host) {
        violations.add("request_outside_test_origin");
      }
      if (tokens.some((token) => request.url().includes(token))) violations.add("token_in_url");
    } catch {
      violations.add("unparseable_request_url");
    }
  });

  const expectedWebSocketProtocol = base.protocol === "https:" ? "wss:" : "ws:";
  await page.routeWebSocket("**/*", async (route) => {
    try {
      const url = new URL(route.url());
      if (!isLoopback(url.hostname) || url.host !== base.host || url.protocol !== expectedWebSocketProtocol) {
        violations.add("request_outside_test_origin");
        await route.close({ code: 1008, reason: "test origin only" });
        return;
      }
      if (tokens.some((token) => url.href.includes(token))) {
        violations.add("token_in_url");
        await route.close({ code: 1008, reason: "credentials are not allowed in URLs" });
        return;
      }
      route.connectToServer();
    } catch {
      violations.add("unparseable_request_url");
      await route.close({ code: 1008, reason: "invalid test request URL" });
    }
  });

  await page.route("**/*", async (route) => {
    const request = route.request();
    let url: URL;
    try {
      url = new URL(request.url());
    } catch {
      violations.add("unparseable_route_url");
      await route.abort();
      return;
    }
    const path = url.pathname;
    const method = request.method().toUpperCase();
    const authorization = await request.headerValue("authorization");
    const role = tokenRole(authorization);
    const isApi = path === "/health" || path.startsWith("/api/") || path.startsWith("/bootstrap-api/");
    const observation: ApiObservation | undefined = isApi
      ? { method, path, auth_role: role, status: null }
      : undefined;
    if (observation) {
      requests.push(observation);
      byRequest.set(request, observation);
    }

    let blocked = false;
    if (!isLoopback(url.hostname) || url.port !== base.port || url.protocol !== base.protocol) {
      violations.add("request_outside_test_origin");
      blocked = true;
    }
    if (!new Set(["GET", "HEAD", "OPTIONS"]).has(method) && !(method === "POST" && allowedPosts.has(path))) {
      violations.add(`mutation_blocked:${method}:${path}`);
      blocked = true;
    }
    if (path.startsWith("/bootstrap-api/v1/") &&
        (role === "ordinary" || role === "other" || role === "none" ||
         (role === "invalid" && path !== "/bootstrap-api/v1/bootstrap/status"))) {
      violations.add("bootstrap_auth_scope_violation");
      blocked = true;
    }
    if (path.startsWith("/api/v1/") &&
        (role === "bootstrap" || role === "other" || role === "none" ||
         (role === "invalid" && path !== "/api/v1/runtime/models"))) {
      violations.add("ordinary_auth_scope_violation");
      blocked = true;
    }
    if (tokens.some((token) => request.url().includes(token))) {
      violations.add("token_in_url");
      blocked = true;
    }

    if (blocked) {
      await route.abort();
      return;
    }
    await route.continue();
  });

  page.on("response", (response) => {
    const request = response.request();
    const observation = byRequest.get(request);
    if (!observation) return;
    observation.status = response.status();
    if (response.status() < 200 || response.status() >= 300) return;
    pending.push((async () => {
      try {
        const summary = responseSummary(observation.path, await response.json());
        if (summary) observation.response_summary = summary;
      } catch {
        // Status and route identity remain useful when a response is non-JSON.
      }
    })());
  });

  page.on("requestfailed", (request) => {
    const observation = byRequest.get(request);
    if (observation) observation.failed = true;
  });

  const recorder: SafetyRecorder = {
    requests,
    violations: [],
    checks,
    async flush() {
      await Promise.all(pending);
      recorder.violations = [...violations].sort();
    },
  };
  return recorder;
}

async function assertNoSecretPersistence(page: Page): Promise<void> {
  const currentUrl = page.url();
  for (const token of tokens) expect(currentUrl).not.toContain(token);
  const persisted = await page.evaluate((secretValues) => {
    const values: string[] = [];
    for (const storage of [window.localStorage, window.sessionStorage]) {
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (key !== null) values.push(key, storage.getItem(key) ?? "");
      }
    }
    return secretValues.some((secret) => values.some((value) => value.includes(secret)));
  }, tokens);
  expect(persisted).toBe(false);
  const cookies = await page.context().cookies();
  expect(cookies.some((cookie) => tokens.some((secret) => cookie.value.includes(secret)))).toBe(false);
  const renderedText = await page.locator("body").innerText();
  for (const token of tokens) expect(renderedText).not.toContain(token);
}

async function expectNoGuardViolations(recorder: SafetyRecorder): Promise<void> {
  await recorder.flush();
  expect(recorder.violations).toEqual([]);
}

function recorderFor(page: Page): SafetyRecorder {
  const recorder = activeRecorders.get(page);
  if (!recorder) throw new Error("network_recorder_missing");
  return recorder;
}

async function waitForApiResponse(page: Page, path: string, method: string) {
  return page.waitForResponse((response) => {
    const request = response.request();
    return new URL(response.url()).pathname === path && request.method() === method;
  });
}

async function connectBootstrap(page: Page, validToken = bootstrapToken): Promise<void> {
  const statusResponse = waitForApiResponse(page, "/bootstrap-api/v1/bootstrap/status", "GET");
  await page.getByLabel("Bootstrap bearer token").fill(validToken);
  await page.getByRole("button", { name: "Connect to bootstrap" }).click();
  const response = await statusResponse;
  expect(response.status()).toBe(200);
  const status = await response.json() as { mode: string; accepting_jobs: boolean };
  expect(status.mode).toBe("bootstrap");
  expect(status.accepting_jobs).toBe(true);
  await expect(page.getByText("Bootstrap connected", { exact: true })).toBeVisible();
}

async function previewBootstrapConfig(page: Page): Promise<void> {
  const configText = JSON.stringify(expected.resolved_config, null, 2);
  await page.getByLabel("Resolved configuration JSON").fill(configText);
  await expect(page.getByText("Configuration needs attention")).toHaveCount(0);
  const previewResponse = waitForApiResponse(page, "/bootstrap-api/v1/bootstrap/preview", "POST");
  await page.getByRole("button", { name: "Preview index build" }).click();
  const response = await previewResponse;
  expect(response.status()).toBe(200);
  const preview = await response.json() as {
    document_count: number;
    manifest_hash: string;
    index_fingerprint: string;
    run_fingerprint: string;
    fresh_build_eligible: boolean;
    existing_index: boolean;
  };
  expect(preview.document_count).toBe(expected.document_count);
  expect(preview.manifest_hash).toBe(expected.manifest_hash);
  expect(preview.index_fingerprint).toBe(expected.index_fingerprint);
  expect(preview.run_fingerprint).toBe(expected.run_fingerprint);
  expect(preview.fresh_build_eligible).toBe(true);
  expect(preview.existing_index).toBe(false);

  const identity = page.getByRole("region", { name: "Server-computed build identity" });
  await expect(identity.getByText(String(expected.document_count), { exact: true })).toBeVisible();
  await expect(identity.getByText(expected.manifest_hash, { exact: true })).toBeVisible();
  await expect(identity.getByText(expected.index_fingerprint, { exact: true })).toBeVisible();
  await expect(identity.getByText(expected.run_fingerprint, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start fresh build" })).toBeDisabled();
  expect(await page.getByLabel(/I explicitly confirm manifest/).isChecked()).toBe(false);
}

test.beforeAll(async ({ browser }) => {
  browserVersion = browser.version();
});

test.beforeEach(async ({ page }) => {
  activeRecorders.set(page, await attachSafetyGuards(page));
});

test.afterEach(async ({ page }, testInfo) => {
  const recorder = activeRecorders.get(page);
  if (!recorder) return;
  await recorder.flush();
  const record = {
    test: testInfo.title,
    status: testInfo.status,
    duration_ms: testInfo.duration,
    browser_version: browserVersion,
    requests: recorder.requests,
    guard_violations: recorder.violations,
    checks: recorder.checks,
  };
  const descriptor = fs.openSync(evidenceFile, "a");
  try {
    fs.writeSync(descriptor, `${JSON.stringify(record)}\n`, undefined, "utf8");
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
});

test.describe("authenticated local workbench workflows", () => {
  test.describe.configure({ mode: "serial" });

  test("bootstrap authentication and preview stay isolated and ephemeral", async ({ page }) => {
    const recorder = recorderFor(page);
    await page.goto("/index-builds");
    await expect(page.getByRole("heading", { name: "Index Builds" })).toBeVisible();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    const editor = page.getByLabel("Resolved configuration JSON");
    await expect(editor).toBeDisabled();
    await expect(page.getByRole("button", { name: "Preview index build" })).toBeDisabled();
    expect(recorder.requests.filter((item) => item.path.startsWith("/bootstrap-api/v1/")).length).toBe(0);
    recorder.checks.push("disconnected_preview_blocked");

    const invalidStatus = waitForApiResponse(page, "/bootstrap-api/v1/bootstrap/status", "GET");
    await page.getByLabel("Bootstrap bearer token").fill(invalidToken);
    await page.getByRole("button", { name: "Connect to bootstrap" }).click();
    const rejected = await invalidStatus;
    expect(rejected.status()).toBe(401);
    const rejectionMessage = await page.getByRole("alert").innerText();
    expect(rejectionMessage).toContain("Authentication failed");
    expect(rejectionMessage).not.toContain(invalidToken);
    recorder.checks.push("invalid_bootstrap_token_rejected_without_secret_echo");
    await assertNoSecretPersistence(page);

    await connectBootstrap(page);
    recorder.checks.push("valid_bootstrap_token_authenticated_by_status");
    await previewBootstrapConfig(page);
    recorder.checks.push("server_preview_identity_matches_synthetic_fixture");
    expect(recorder.requests.filter((item) => item.path === "/bootstrap-api/v1/bootstrap/preview" && item.method === "POST").length).toBe(1);
    expect(recorder.requests.some((item) => item.path.startsWith("/api/v1/") && item.auth_role === "bootstrap")).toBe(false);
    expect(recorder.requests.some((item) => item.path === "/bootstrap-api/v1/bootstrap/jobs" && item.method === "POST")).toBe(false);
    await assertNoSecretPersistence(page);

    await page.getByRole("button", { name: "Disconnect bootstrap" }).click();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await expect(editor).toBeDisabled();
    await expect(editor).toHaveValue("");
    await expect(page.getByRole("heading", { name: "Server-computed build identity" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Build report inventory" })).toHaveCount(0);
    await assertNoSecretPersistence(page);
    recorder.checks.push("disconnect_cleared_bootstrap_session_and_config");

    const beforeReload = recorder.requests.length;
    await page.reload();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Bootstrap bearer token")).toHaveValue("");
    await expect(editor).toBeDisabled();
    expect(recorder.requests.slice(beforeReload).some((item) => item.path.startsWith("/bootstrap-api/v1/"))).toBe(false);
    await assertNoSecretPersistence(page);
    await expectNoGuardViolations(recorder);
    recorder.checks.push("reload_required_bootstrap_authentication");
  });

  test("build report inventory is read-only and empty", async ({ page }) => {
    const recorder = recorderFor(page);
    await page.goto("/index-builds");
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();

    const jobsResponsePromise = waitForApiResponse(page, "/bootstrap-api/v1/bootstrap/jobs", "GET");
    await connectBootstrap(page);
    const jobsResponse = await jobsResponsePromise;
    expect(jobsResponse.status()).toBe(200);
    expect((await jobsResponse.json() as { jobs: unknown[] }).jobs).toEqual([]);
    await previewBootstrapConfig(page);

    const inventoryResponsePromise = waitForApiResponse(page, "/bootstrap-api/v1/bootstrap/index-inventory", "POST");
    await page.getByRole("button", { name: "Inspect build reports" }).click();
    const inventoryResponse = await inventoryResponsePromise;
    expect(inventoryResponse.status()).toBe(200);
    const inventory = await inventoryResponse.json() as {
      complete: boolean;
      total_candidates: number;
      candidates: unknown[];
      store_checked: boolean;
      check_level: string;
    };
    expect(inventory.complete).toBe(true);
    expect(inventory.total_candidates).toBe(0);
    expect(inventory.candidates).toEqual([]);
    expect(inventory.store_checked).toBe(false);
    expect(inventory.check_level).toBe("report");
    await expect(page.getByText("No build reports were found in the trusted inventory.")).toBeVisible();
    await expect(page.getByText("Report compatibility only.", { exact: false })).toBeVisible();
    await expect(page.getByText("Chroma collections were not checked.", { exact: false })).toBeVisible();
    expect(recorder.requests.filter((item) => item.path === "/bootstrap-api/v1/bootstrap/index-inventory" && item.method === "POST").length).toBe(1);
    expect(recorder.requests.some((item) => item.path === "/bootstrap-api/v1/bootstrap/jobs" && item.method === "POST")).toBe(false);
    expect(recorder.requests.some((item) => item.method !== "GET" && item.path.includes("/repair"))).toBe(false);
    expect(recorder.requests.some((item) => item.method !== "GET" && item.path.includes("/activate"))).toBe(false);
    await assertNoSecretPersistence(page);
    recorder.checks.push("empty_inventory_store_not_checked_no_mutation");

    await page.getByRole("button", { name: "Disconnect bootstrap" }).click();
    await expect(page.getByLabel("Resolved configuration JSON")).toHaveValue("");
    await expect(page.getByRole("heading", { name: "Build report inventory" })).toHaveCount(0);
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Bootstrap bearer token")).toHaveValue("");
    await assertNoSecretPersistence(page);
    await expectNoGuardViolations(recorder);
    recorder.checks.push("inventory_cleared_on_disconnect_and_reload");
  });

  test("ordinary workbench authentication and catalogs use only ordinary API token", async ({ page }) => {
    const recorder = recorderFor(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Workbench overview" })).toBeVisible();
    await expect(page.getByText("API process is responding")).toBeVisible();
    await expect(page.getByText("Liveness only", { exact: false })).toBeVisible();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    expect(recorder.requests.some((item) => item.path === "/health" && item.status === 200 && item.auth_role === "none")).toBe(true);
    expect(recorder.requests.some((item) => item.path.startsWith("/api/v1/") && item.auth_role === "none")).toBe(false);
    recorder.checks.push("unauthenticated_liveness_separate_from_readiness");

    const invalidModelsPromise = waitForApiResponse(page, "/api/v1/runtime/models", "GET");
    await page.getByLabel("Bearer token").fill(invalidToken);
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    const rejected = await invalidModelsPromise;
    expect(rejected.status()).toBe(401);
    const rejectedMessage = await page.getByRole("alert").innerText();
    expect(rejectedMessage).toContain("Authentication failed");
    expect(rejectedMessage).not.toContain(invalidToken);
    recorder.checks.push("invalid_ordinary_token_rejected_without_secret_echo");
    await assertNoSecretPersistence(page);

    const acceptedModelsPromise = waitForApiResponse(page, "/api/v1/runtime/models", "GET");
    await page.getByLabel("Bearer token").fill(ordinaryToken);
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    const accepted = await acceptedModelsPromise;
    expect(accepted.status()).toBe(200);
    expect((await accepted.json() as { components: unknown[] }).components).toEqual([]);
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    await expect(page.getByText("No adapters are registered yet.", { exact: false })).toBeVisible();
    await expect(page.getByText("This status does not indicate index or Ollama readiness.", { exact: false })).toBeVisible();
    await expect(page.getByText("No jobs registered", { exact: true })).toBeVisible();
    await expect(page.getByText("No result artifacts", { exact: true })).toBeVisible();
    expect(recorder.requests.filter((item) => item.path.startsWith("/api/v1/") && item.auth_role === "ordinary").length).toBeGreaterThan(0);
    expect(recorder.requests.some((item) => item.path.startsWith("/bootstrap-api/") && item.auth_role === "ordinary")).toBe(false);
    await assertNoSecretPersistence(page);
    recorder.checks.push("ordinary_models_and_empty_catalogs_from_server");

    const jobsPromise = waitForApiResponse(page, "/api/v1/jobs", "GET");
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Jobs", exact: true }).click();
    const jobs = await jobsPromise;
    expect(jobs.status()).toBe(200);
    expect((await jobs.json() as { jobs: unknown[] }).jobs).toEqual([]);
    await expect(page.getByRole("heading", { name: "Jobs", exact: true })).toBeVisible();
    await expect(page.getByText("Submitted jobs will appear here.")).toBeVisible();

    const artifactsPromise = waitForApiResponse(page, "/api/v1/artifacts", "GET");
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Artifacts", exact: true }).click();
    const artifacts = await artifactsPromise;
    expect(artifacts.status()).toBe(200);
    expect((await artifacts.json() as { artifacts: unknown[] }).artifacts).toEqual([]);
    await expect(page.getByRole("heading", { name: "Artifacts", exact: true })).toBeVisible();
    await expect(page.getByText("Registered evaluations and ablations will appear here.")).toBeVisible();
    expect(recorder.requests.some((item) => item.method === "POST" && item.path.startsWith("/api/v1/"))).toBe(false);
    expect(recorder.requests.some((item) => item.path.startsWith("/bootstrap-api/") && item.auth_role === "ordinary")).toBe(false);
    await assertNoSecretPersistence(page);
    recorder.checks.push("jobs_and_artifacts_navigation_read_only_and_empty");

    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Bearer token")).toHaveValue("");
    await assertNoSecretPersistence(page);
    await page.reload();
    await expect(page.getByText("Disconnected", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Bearer token")).toHaveValue("");
    await expect(page.getByText("Connect to view this page")).toBeVisible();
    await assertNoSecretPersistence(page);
    await expectNoGuardViolations(recorder);
    recorder.checks.push("disconnect_and_reload_cleared_ordinary_authentication");
  });
});
