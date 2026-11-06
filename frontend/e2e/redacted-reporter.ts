import fs from "node:fs";
import path from "node:path";
import type { FullConfig, FullResult, Reporter, Suite, TestCase, TestError, TestResult } from "@playwright/test/reporter";

const knownTitles = new Set([
  "bootstrap authentication and preview stay isolated and ephemeral",
  "build report inventory is read-only and empty",
  "ordinary workbench authentication and catalogs use only ordinary API token",
]);
const knownPhases = new Set([
  "browser_page_navigation",
  "initial_disconnected_state",
  "invalid_bootstrap_authentication",
  "valid_bootstrap_authentication",
  "bootstrap_preview",
  "bootstrap_inventory",
  "bootstrap_disconnect",
  "bootstrap_reload",
  "final_integrity",
  "invalid_ordinary_authentication",
  "valid_ordinary_authentication",
  "ordinary_catalog_navigation",
  "ordinary_disconnect",
  "ordinary_reload",
]);
const knownStatuses = new Set(["passed", "failed", "timedOut", "skipped", "interrupted"]);
const knownFailureCategories = new Set([
  "locator_ambiguous",
  "expectation_timeout",
  "navigation_failed",
  "browser_or_context_failed",
  "test_setup_failed",
  "assertion_failed",
  "unknown_test_failure",
]);

type FailureCategory =
  | "locator_ambiguous"
  | "expectation_timeout"
  | "navigation_failed"
  | "browser_or_context_failed"
  | "test_setup_failed"
  | "assertion_failed"
  | "unknown_test_failure";

type FailurePhase = "setup" | "execution" | "teardown";

interface FailureRecord {
  category: FailureCategory;
  phase: string | null;
  failure_phase: FailurePhase;
  source_file?: "frontend/e2e/authenticated-workflows.spec.ts";
  line?: number;
  column?: number;
}

interface TestRecord {
  title: string;
  status: string;
  duration_ms: number;
  retry: number;
  failure?: FailureRecord;
}

interface ReporterOptions {
  outputFile: string;
}

function boundedInteger(value: unknown, maximum: number): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 && value <= maximum
    ? value
    : undefined;
}

function currentPhase(result: TestResult): string | null {
  for (let index = result.annotations.length - 1; index >= 0; index -= 1) {
    const annotation = result.annotations[index];
    if (annotation?.type !== "e2e_phase") continue;
    return typeof annotation.description === "string" && knownPhases.has(annotation.description)
      ? annotation.description
      : null;
  }
  return null;
}

function safeLocation(error: TestError | undefined): Pick<FailureRecord, "source_file" | "line" | "column"> {
  const location = error?.location;
  if (!location || typeof location.file !== "string") return {};
  const approvedAbsolute = path.resolve(process.cwd(), "e2e", "authenticated-workflows.spec.ts");
  if (path.resolve(location.file) !== approvedAbsolute) return {};
  const line = boundedInteger(location.line, 10000);
  if (line === undefined) return {};
  const column = boundedInteger(location.column, 10000);
  return {
    source_file: "frontend/e2e/authenticated-workflows.spec.ts",
    line,
    ...(column === undefined ? {} : { column }),
  };
}

function classifyFailure(error: TestError | undefined, phase: string | null, failurePhase: FailurePhase): FailureCategory {
  if (!error) return failurePhase === "setup" ? "test_setup_failed" : "unknown_test_failure";
  const message = typeof error.message === "string" ? error.message : "";
  const errorName = typeof (error as TestError & { name?: unknown }).name === "string"
    ? (error as TestError & { name: string }).name
    : "";

  if (/strict mode violation|resolved to\s+\d+\s+elements/i.test(message)) return "locator_ambiguous";
  if (errorName === "TimeoutError" || /expect\(received\).*timed out|timed out .*expect/i.test(message)) {
    return "expectation_timeout";
  }
  if (phase === "browser_page_navigation") return "navigation_failed";
  if (/target closed|browser (?:has been )?closed|browser context.*closed|page.*closed/i.test(message)) {
    return "browser_or_context_failed";
  }
  if (failurePhase === "setup") return "test_setup_failed";
  if (/expect\(|expected:|received:/i.test(message)) return "assertion_failed";
  return "unknown_test_failure";
}

function failureRecord(result: TestResult): FailureRecord {
  const phase = currentPhase(result);
  const failurePhase: FailurePhase = phase === "final_integrity"
    ? "teardown"
    : phase === null
      ? "setup"
      : "execution";
  const error = result.error ?? result.errors[0];
  const category = classifyFailure(error, phase, failurePhase);
  return {
    category: knownFailureCategories.has(category)
      ? category
      : "unknown_test_failure",
    phase,
    failure_phase: failurePhase,
    ...safeLocation(error),
  };
}

function safeStatus(status: string): string {
  return knownStatuses.has(status) ? status : "interrupted";
}

export default class RedactedReporter implements Reporter {
  private readonly outputFile: string;
  private readonly tests: TestRecord[] = [];
  private expected = 0;

  constructor(options: ReporterOptions) {
    this.outputFile = options.outputFile;
  }

  onBegin(_config: FullConfig, suite: Suite): void {
    this.expected = Math.min(suite.allTests().length, 10);
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    const status = safeStatus(result.status);
    const failure = status === "failed" || status === "timedOut" || status === "interrupted"
      ? failureRecord(result)
      : undefined;
    const duration = Number.isFinite(result.duration) ? Math.max(0, Math.min(Math.round(result.duration), 1800000)) : 0;
    const retry = Number.isSafeInteger(result.retry) ? Math.max(0, Math.min(result.retry, 2)) : 0;
    this.tests.push({
      title: knownTitles.has(test.title) ? test.title : "unknown_test",
      status,
      duration_ms: duration,
      retry,
      ...(failure ? { failure } : {}),
    });
  }

  onEnd(result: FullResult): void {
    fs.mkdirSync(path.dirname(this.outputFile), { recursive: true });
    const payload = {
      schema_version: 1,
      status: ["passed", "failed", "timedout", "interrupted"].includes(result.status) ? result.status : "interrupted",
      expected_tests: this.expected,
      completed_tests: this.tests.length,
      tests: this.tests,
    };
    const descriptor = fs.openSync(this.outputFile, "w");
    try {
      fs.writeSync(descriptor, `${JSON.stringify(payload, null, 2)}\n`, undefined, "utf8");
      fs.fsyncSync(descriptor);
    } finally {
      fs.closeSync(descriptor);
    }
  }
}
