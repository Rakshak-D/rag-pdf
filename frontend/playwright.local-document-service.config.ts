import { defineConfig, devices } from "@playwright/test";

const host = "127.0.0.1";
const appPort = 4313;
const appUrl = `http://${host}:${appPort}`;

export default defineConfig({
  testDir: "./e2e/local-document-service",
  testMatch: "**/*.spec.ts",
  outputDir: "./test-results/local-document-service",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 12_000 },
  reporter: [["line"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: appUrl,
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "python ../scripts/local_pdf_service_stack.py",
    cwd: process.cwd(),
    url: appUrl,
    reuseExistingServer: false,
    timeout: 90_000,
    gracefulShutdown: { signal: "SIGINT", timeout: 5_000 },
    env: {
      M9B2_ORDINARY_PORT: "4310",
      M9B2_BOOTSTRAP_PORT: "4311",
      M9B2_GATEWAY_PORT: "4312",
      M9B2_VITE_PORT: String(appPort),
    },
  },
});
