export interface BrowserSelection {
  browserName: "chromium";
  channel?: "msedge";
  executablePath?: string;
}

export function selectPlaywrightBrowser(
  configured: string | undefined,
  platform: string,
  chromiumExecutablePath?: string,
): BrowserSelection {
  const selected = configured?.trim() || (platform === "win32" ? "msedge" : "chromium");
  if (selected !== "msedge" && selected !== "chromium") {
    throw new Error("playwright_browser_selection_invalid");
  }
  if (selected === "msedge") {
    if (platform !== "win32") throw new Error("playwright_browser_not_supported_on_platform");
    return { browserName: "chromium", channel: "msedge" };
  }
  if (chromiumExecutablePath !== undefined) {
    const path = chromiumExecutablePath.trim();
    if (!path.startsWith("/") || path.includes("\0") || path.includes("\n")) {
      throw new Error("playwright_chromium_path_invalid");
    }
    return { browserName: "chromium", executablePath: path };
  }
  return { browserName: "chromium" };
}
