import fs from "node:fs";
import path from "node:path";
import type { FullConfig, FullResult, Reporter, Suite, TestCase, TestResult } from "@playwright/test/reporter";

interface TestRecord {
  title: string;
  status: string;
  duration_ms: number;
  retry: number;
}

interface ReporterOptions {
  outputFile: string;
}

export default class RedactedReporter implements Reporter {
  private readonly outputFile: string;
  private readonly tests: TestRecord[] = [];
  private expected = 0;

  constructor(options: ReporterOptions) {
    this.outputFile = options.outputFile;
  }

  onBegin(_config: FullConfig, suite: Suite): void {
    this.expected = suite.allTests().length;
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    this.tests.push({
      title: test.title,
      status: result.status,
      duration_ms: result.duration,
      retry: result.retry,
    });
  }

  onEnd(result: FullResult): void {
    fs.mkdirSync(path.dirname(this.outputFile), { recursive: true });
    const payload = {
      status: result.status,
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
