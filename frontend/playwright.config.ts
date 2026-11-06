import { defineConfig } from "@playwright/test";
import { selectPlaywrightBrowser } from "./e2e/browser-selection";

const baseURL = process.env.E2E_BASE_URL;
const outputDir = process.env.PLAYWRIGHT_OUTPUT_DIR;
const resultFile = process.env.E2E_RESULT_FILE;
const browser = selectPlaywrightBrowser(
  process.env.E2E_BROWSER,
  process.platform,
  process.env.E2E_BROWSER_EXECUTABLE_PATH,
);

if (!baseURL || !outputDir || !resultFile) {
  throw new Error("authenticated_e2e_environment_missing");
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "authenticated-workflows.spec.ts",
  fullyParallel: false,
  forbidOnly: true,
  workers: 1,
  retries: 0,
  maxFailures: 1,
  timeout: 120_000,
  globalTimeout: 600_000,
  outputDir,
  reporter: [["./e2e/redacted-reporter.ts", { outputFile: resultFile }]],
  use: {
    baseURL,
    browserName: browser.browserName,
    ...(browser.channel ? { channel: browser.channel } : {}),
    ...(browser.executablePath ? { launchOptions: { executablePath: browser.executablePath } } : {}),
    headless: true,
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
    screenshot: "off",
    trace: "off",
    video: "off",
  },
});
