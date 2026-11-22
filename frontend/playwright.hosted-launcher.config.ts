import { defineConfig, devices } from "@playwright/test";

if (!process.env.M9C2B_APP_URL) throw new Error("Hosted launcher URL is required");
export default defineConfig({
  testDir: "./e2e/hosted-launcher",
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 12_000 },
  reporter: "line",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.M9C2B_APP_URL,
    headless: true,
    serviceWorkers: "block",
  },
});
