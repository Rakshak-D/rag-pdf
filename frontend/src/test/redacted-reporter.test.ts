import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { FullResult, Suite, TestCase, TestError, TestResult } from "@playwright/test/reporter";
import RedactedReporter from "../../e2e/redacted-reporter";

const testTitle = "bootstrap authentication and preview stay isolated and ephemeral";
const temporaryDirectories: string[] = [];

function makeReport(
  status: TestResult["status"],
  error?: TestError,
  annotations: TestResult["annotations"] = [],
  title = testTitle,
) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "rag-redacted-reporter-"));
  temporaryDirectories.push(directory);
  const outputFile = path.join(directory, "playwright-results.json");
  const reporter = new RedactedReporter({ outputFile });
  reporter.onBegin({} as never, { allTests: () => [{}, {}, {}] } as unknown as Suite);
  reporter.onTestEnd(
    { title } as TestCase,
    {
      status,
      duration: 25,
      retry: 0,
      error,
      errors: error ? [error] : [],
      annotations,
    } as TestResult,
  );
  reporter.onEnd({ status: status === "passed" ? "passed" : "failed" } as FullResult);
  const text = fs.readFileSync(outputFile, "utf8");
  return { payload: JSON.parse(text) as Record<string, unknown>, text };
}

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe("redacted Playwright reporter", () => {
  it("writes bounded success records without a failure payload", () => {
    const { payload } = makeReport("passed");
    expect(payload).toMatchObject({ schema_version: 1, expected_tests: 3, completed_tests: 1 });
    expect(payload.tests).toEqual([{ title: testTitle, status: "passed", duration_ms: 25, retry: 0 }]);
  });

  it("records an allowlisted locator failure and verified test-source location", () => {
    const source = path.resolve(process.cwd(), "e2e", "authenticated-workflows.spec.ts");
    const { payload } = makeReport(
      "failed",
      { message: "strict mode violation: locator resolved to 2 elements", location: { file: source, line: 358, column: 11 } },
      [{ type: "e2e_phase", description: "initial_disconnected_state" }],
    );
    const tests = payload.tests as Array<{ failure?: Record<string, unknown> }>;
    expect(tests[0]?.failure).toEqual({
      category: "locator_ambiguous",
      phase: "initial_disconnected_state",
      failure_phase: "execution",
      source_file: "frontend/e2e/authenticated-workflows.spec.ts",
      line: 358,
      column: 11,
    });
  });

  it("classifies a failure before any phase marker as setup", () => {
    const { payload } = makeReport("failed", { message: "module setup failed" });
    const tests = payload.tests as Array<{ failure?: Record<string, unknown> }>;
    expect(tests[0]?.failure).toEqual({
      category: "test_setup_failed",
      phase: null,
      failure_phase: "setup",
    });
  });

  it.each([
    ["expectation timeout", { name: "TimeoutError", message: "operation exceeded timeout" }, "initial_disconnected_state", "expectation_timeout"],
    ["navigation failure", { message: "navigation failed" }, "browser_page_navigation", "navigation_failed"],
    ["browser context failure", { message: "Target closed while waiting" }, "valid_bootstrap_authentication", "browser_or_context_failed"],
    ["assertion failure", { message: "expect(received).toBe(expected)" }, "bootstrap_preview", "assertion_failed"],
    ["unknown failure", { message: "opaque failure" }, "bootstrap_preview", "unknown_test_failure"],
  ] as const)("classifies %s conservatively", (_label, error, phase, category) => {
    const { payload } = makeReport(
      "failed",
      error as TestError,
      [{ type: "e2e_phase", description: phase }],
    );
    const tests = payload.tests as Array<{ failure?: Record<string, unknown> }>;
    expect(tests[0]?.failure?.category).toBe(category);
  });

  it("never persists exception text, credentials, paths, URLs, headers or payloads", () => {
    const malicious = [
      "Bearer SYNTHETIC_SECRET_8f1a",
      "C:\\Users\\private\\profile\\document.pdf",
      "/home/private/document.pdf",
      "https://user:password@example.invalid/path?token=secret",
      "Authorization: Bearer header-secret",
      '{"question":"private payload"}',
      "\u001b[31mANSI\u001b[0m",
      "first line\nsecond line",
      "oversized-".repeat(20000),
    ].join(" ");
    const { text } = makeReport("failed", { message: malicious });
    for (const secret of ["SYNTHETIC_SECRET_8f1a", "private\\profile", "/home/private", "user:password", "header-secret", "private payload", "ANSI", "oversized-"]) {
      expect(text).not.toContain(secret);
    }
    expect(text).not.toContain("\u001b");
    expect(text).not.toContain("Authorization:");
  });

  it("rejects unapproved locations and unknown test titles", () => {
    const { payload } = makeReport(
      "failed",
      { message: "assertion failed: expected value", location: { file: "/home/runner/unrelated.spec.ts", line: 4, column: 2 } },
      [{ type: "e2e_phase", description: "not-a-phase" }],
      "Bearer title-secret https://user:pass@host/?token=x",
    );
    const tests = payload.tests as Array<{ title: string; failure?: Record<string, unknown> }>;
    expect(tests[0]?.title).toBe("unknown_test");
    expect(tests[0]?.failure).toEqual({
      category: "test_setup_failed",
      phase: null,
      failure_phase: "setup",
    });
    expect(JSON.stringify(payload)).not.toContain("title-secret");
    expect(JSON.stringify(payload)).not.toContain("unrelated.spec.ts");
  });
});
