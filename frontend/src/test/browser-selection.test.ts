import { describe, expect, it } from "vitest";
import { selectPlaywrightBrowser } from "../../e2e/browser-selection";

describe("Playwright browser selection", () => {
  it("keeps Microsoft Edge as the local Windows default", () => {
    expect(selectPlaywrightBrowser(undefined, "win32")).toEqual({
      browserName: "chromium",
      channel: "msedge",
    });
  });

  it("selects Chromium by default outside Windows", () => {
    expect(selectPlaywrightBrowser(undefined, "linux")).toEqual({ browserName: "chromium" });
  });

  it("accepts only an absolute runner-provided Chromium executable", () => {
    expect(selectPlaywrightBrowser("chromium", "linux", "/usr/bin/chromium")).toEqual({
      browserName: "chromium",
      executablePath: "/usr/bin/chromium",
    });
    expect(() => selectPlaywrightBrowser("chromium", "linux", "relative/chromium")).toThrow(
      "playwright_chromium_path_invalid",
    );
  });

  it("rejects unsupported choices and Edge on Linux", () => {
    expect(() => selectPlaywrightBrowser("firefox", "linux")).toThrow(
      "playwright_browser_selection_invalid",
    );
    expect(() => selectPlaywrightBrowser("msedge", "linux")).toThrow(
      "playwright_browser_not_supported_on_platform",
    );
  });
});
