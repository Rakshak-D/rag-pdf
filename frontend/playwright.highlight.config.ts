import { defineConfig, devices } from "@playwright/test";

const host = "127.0.0.1";
const appUrl = `http://${host}:4213`;
const fixtureUrl = `http://${host}:4214`;

export default defineConfig({
  testDir: "./e2e/highlight",
  testMatch: "**/*.spec.ts",
  outputDir: "./test-results/nasa-highlight",
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
    deviceScaleFactor: 2,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "node e2e/highlight/fixture-server.mjs",
      cwd: process.cwd(),
      url: `${fixtureUrl}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
      gracefulShutdown: { signal: "SIGINT", timeout: 5_000 },
      env: { NASA_FIXTURE_PORT: "4214" },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 4213 --strictPort",
      cwd: process.cwd(),
      url: appUrl,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        VITE_RAG_LOCAL_GATEWAY: "true",
        RAG_WORKBENCH_LOCAL_GATEWAY_TARGET: fixtureUrl,
      },
    },
  ],
});
