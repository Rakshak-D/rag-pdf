import { expect, test, type Page } from "@playwright/test";

const LINK_QUESTION = "What does the rock Link tell us about water on Mars?";
const POWER_QUESTION = "How much power did the rover have at launch?";

type CitationResponse = {
  answer: string;
  referenced_citations: string[];
  text_evidence: Array<{ label: string; page_number: number | null; passage: string }>;
  image_evidence: Array<{ label: string; page_number: number | null; caption: string }>;
};

test.beforeEach(async ({ page, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  const outside: string[] = [];
  (page as Page & { __outsideRequests?: string[] }).__outsideRequests = outside;
  page.on("request", (request) => {
    const requestOrigin = new URL(request.url()).origin;
    if (requestOrigin !== origin) outside.push(requestOrigin);
  });
  await page.route("**/*", async (route) => {
    if (new URL(route.request().url()).origin !== origin) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  await page.addInitScript(() => sessionStorage.clear());
});

async function assertLoopbackOnly(page: Page): Promise<void> {
  const outside = (page as Page & { __outsideRequests?: string[] }).__outsideRequests ?? [];
  expect(outside, `unexpected browser network origins: ${outside.join(", ")}`).toEqual([]);
}

async function openDesk(page: Page, width = 1440, height = 900): Promise<void> {
  await page.setViewportSize({ width, height });
  await page.goto("/");
  await expect(page.getByTestId("sample-pdf-preview")).toBeVisible();
  await expect(page.getByRole("button", { name: LINK_QUESTION })).toBeVisible();
}

async function ask(page: Page, question = LINK_QUESTION): Promise<void> {
  const suggestion = page.getByRole("button", { name: question });
  if (await suggestion.isVisible().catch(() => false)) {
    await suggestion.click();
  } else {
    await page.locator("#chat-question").fill(question);
  }
  await expect(page.locator("#chat-question")).toHaveValue(question);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".chat-message--assistant").last()).toBeVisible();
}

async function openPopover(page: Page, accessibleName: string) {
  const citation = page.getByRole("button", { name: accessibleName });
  await citation.click();
  const regionId = await citation.getAttribute("aria-controls");
  expect(regionId).toBeTruthy();
  const region = page.locator(`#${regionId}`);
  await expect(region).toBeVisible();
  return { citation, region };
}

test("accessible citation names describe source type and actual evidence page", async ({ page }) => {
  await page.route("**/api/v1/ask", async (route) => {
    const upstream = await route.fetch();
    const value = await upstream.json() as CitationResponse;
    value.answer += " The image shows the outcrop [I1].";
    value.referenced_citations.push("I1");
    await route.fulfill({ response: upstream, json: value });
  });
  await openDesk(page);
  await ask(page);

  const text = page.getByRole("button", { name: "Text source T1, page 2" });
  const figure = page.getByRole("button", { name: "Figure source I1, page 2" });
  await expect(text).toHaveText("[T1]");
  await expect(figure).toHaveText("[I1]");

  const textPopover = await openPopover(page, "Text source T1, page 2");
  await expect(textPopover.region).toHaveAttribute("aria-label", "Source T1");
  await expect(textPopover.region).toContainText("deposition of water");
  await expect(text).toHaveAttribute("aria-expanded", "true");

  await figure.click();
  await expect(text).toHaveAttribute("aria-expanded", "false");
  await expect(figure).toHaveAttribute("aria-expanded", "true");
  const figureRegionId = await figure.getAttribute("aria-controls");
  expect(figureRegionId).toBeTruthy();
  const figureRegion = page.locator(`#${figureRegionId}`);
  await expect(figureRegion).toHaveAttribute("aria-label", "Source I1");
  await expect(figureRegion).toContainText("Mastcam view");
  await expect(figureRegion).not.toContainText("deposition of water");
  await assertLoopbackOnly(page);
});

test("unknown page is named honestly and has no misleading Open page action", async ({ page }) => {
  await page.route("**/api/v1/ask", async (route) => {
    const upstream = await route.fetch();
    const value = await upstream.json() as CitationResponse;
    value.text_evidence[0]!.page_number = null;
    await route.fulfill({ response: upstream, json: value });
  });
  await openDesk(page);
  await ask(page);

  const { citation, region } = await openPopover(page, "Text source T1, page not identified");
  await expect(citation).toHaveText("[T1]");
  await expect(region.getByText("Page unavailable")).toHaveCount(1);
  await expect(region.getByRole("button", { name: /Open page/ })).toHaveCount(0);
  const regionId = await region.getAttribute("id");
  if (!regionId) throw new Error("The active source region must have an id");
  await expect(citation).toHaveAttribute("aria-controls", regionId);
  await assertLoopbackOnly(page);
});

test("keyboard activation, source switching, page opening and focus restoration work", async ({ page }) => {
  await openDesk(page);
  await ask(page);

  const t1 = page.getByRole("button", { name: "Text source T1, page 2" });
  const t2 = page.getByRole("button", { name: "Text source T2, page 2" });
  const t3 = page.getByRole("button", { name: "Text source T3, page 2" });
  await t1.hover();
  await expect(page.getByRole("region", { name: "Source T1" })).toHaveCount(0);

  await t1.focus();
  await page.keyboard.press("Enter");
  const firstRegion = page.getByRole("region", { name: "Source T1" });
  await expect(firstRegion).toContainText("deposition of water");
  await page.keyboard.press("Escape");
  await expect(firstRegion).toHaveCount(0);
  await expect(t1).toBeFocused();

  await t2.focus();
  await page.keyboard.press("Space");
  const secondRegion = page.getByRole("region", { name: "Source T2" });
  await expect(secondRegion).toContainText("rounded pebbles");
  await expect(page.getByRole("region", { name: "Source T1" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(t2).toBeFocused();

  await t1.click();
  await t2.click();
  await expect(page.getByRole("region", { name: "Source T2" })).toContainText("hardened sand");
  await expect(page.getByRole("region", { name: "Source T2" })).not.toContainText("deposition of water");
  await t3.click();
  const thirdRegion = page.getByRole("region", { name: "Source T3" });
  await expect(thirdRegion).toContainText("John Klein");
  await expect(page.getByRole("region", { name: "Source T2" })).toHaveCount(0);

  await t3.focus();
  await page.keyboard.press("Tab");
  const openPage = thirdRegion.getByRole("button", { name: "Open page 2" });
  await expect(openPage).toBeFocused();
  await page.keyboard.press("Enter");
  const viewer = page.getByRole("dialog", { name: "PDF viewer" });
  await expect(viewer).toBeVisible();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-document-id", /^doc_[0-9a-f]{64}$/);
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "2");
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(t3).toBeFocused();

  await thirdRegion.getByRole("button", { name: "Open page 2" }).click();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "2");
  await page.getByRole("button", { name: "Close PDF viewer" }).click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveCount(0);
  await expect(t3).toBeFocused();
  await assertLoopbackOnly(page);
});

test("reused citation labels remain tied to their originating answers", async ({ page }) => {
  await openDesk(page);
  await ask(page);
  const firstCitation = page.getByRole("button", { name: "Text source T1, page 2" });

  await ask(page, POWER_QUESTION);
  const secondCitation = page.getByRole("button", { name: "Text source T1, page 3" });
  await expect(secondCitation).toBeVisible();
  await expect(page.getByRole("button", { name: "Text source T1, page 2" })).toHaveCount(1);

  const second = await openPopover(page, "Text source T1, page 3");
  await expect(second.region).toContainText("110 watts of electrical power");
  const secondRegionId = await second.region.getAttribute("id");
  if (!secondRegionId) throw new Error("The active second source region must have an id");
  await expect(second.citation).toHaveAttribute("aria-controls", secondRegionId);

  await firstCitation.click();
  const firstRegionId = await firstCitation.getAttribute("aria-controls");
  expect(firstRegionId).toBeTruthy();
  const firstRegion = page.locator(`#${firstRegionId}`);
  await expect(firstRegion).toBeVisible();
  await expect(firstRegion).toContainText("deposition of water");
  await expect(firstRegion).not.toContainText("110 watts");
  await expect(second.region).toContainText("110 watts of electrical power");
  await assertLoopbackOnly(page);
});

test("mobile modeless sheet permits Tab to leave; modal snaps contain keyboard focus", async ({ page }) => {
  await openDesk(page, 390, 844);
  await ask(page);
  const citation = page.getByRole("button", { name: "Text source T3, page 2" });

  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  let citationReached = false;
  for (let step = 0; step < 60 && !citationReached; step += 1) {
    await page.keyboard.press("Tab");
    citationReached = await citation.evaluate((node) => node === document.activeElement);
  }
  expect(citationReached, "citation must be reachable through sequential keyboard navigation").toBe(true);
  await page.keyboard.press("Enter");
  const region = page.getByRole("region", { name: "Source T3" });
  await expect(region).toBeVisible();
  await region.getByRole("button", { name: "Open page 2" }).click();

  const viewer = page.getByRole("dialog", { name: "PDF viewer" });
  await expect(viewer).toHaveAttribute("data-mobile-snap", "30");
  await expect(viewer).not.toHaveAttribute("aria-modal", "true");
  await expect(page.getByTestId("pdf-page-frame")).toBeVisible();
  await expect(page.getByTestId("show-matches")).toBeVisible();
  const focusable = viewer.locator("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])");
  const focusableCount = await focusable.count();
  expect(focusableCount).toBeGreaterThan(1);
  await focusable.nth(focusableCount - 1).focus();
  await page.keyboard.press("Tab");
  await expect(viewer.locator(".pdf-viewer__canvas-scroll")).toBeFocused();
  await page.keyboard.press("Tab");
  expect(await viewer.evaluate((node) => node.contains(document.activeElement)), "30% sheet must allow focus to leave after its scroll region").toBe(false);

  const snap55 = page.getByRole("button", { name: /PDF sheet 30 percent/ });
  await snap55.focus();
  await page.keyboard.press("Enter");
  await expect(viewer).toHaveAttribute("data-mobile-snap", "55");
  await expect(viewer).toHaveAttribute("aria-modal", "true");
  const modalFocusables = viewer.locator("button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])");
  const modalCount = await modalFocusables.count();
  const first = modalFocusables.nth(0);
  const last = modalFocusables.nth(modalCount - 1);
  await last.focus();
  await page.keyboard.press("Tab");
  await expect(first).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(last).toBeFocused();

  await page.getByRole("button", { name: /PDF sheet 55 percent/ }).focus();
  await page.keyboard.press("Enter");
  await expect(viewer).toHaveAttribute("data-mobile-snap", "92");
  await expect(viewer).toHaveAttribute("aria-modal", "true");
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(citation).toBeFocused();
  await assertLoopbackOnly(page);
});

test("existing copy and source-navigation feedback remains announced once", async ({ page }) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await openDesk(page);
  await ask(page);
  await page.getByRole("button", { name: "Copy answer" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Answer copied" })).toHaveCount(1);

  const { region } = await openPopover(page, "Text source T1, page 2");
  await region.getByRole("button", { name: "Open page 2" }).click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toBeVisible();
  await expect(page.locator(".pdf-viewer__pages [aria-live='polite']")).toContainText("2");
  await expect(page.locator(".pdf-viewer-context")).toContainText("Citation · page 2");
  await assertLoopbackOnly(page);
});
