import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const SAMPLE_SHA = "2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6";
const DOCUMENT_ID = `doc_${SAMPLE_SHA}`;
const MANIFEST_HASH = "b".repeat(64);
const LINK_QUESTION = "What does the rock Link tell us about water on Mars?";
const POWER_QUESTION = "How much power did the rover have at launch?";
const CRATER_QUESTION = "Which crater did it land in, and what did it drill first?";

type PdfBox = [number, number, number, number];
type CaseDefinition = { label: string; expectedText: string; boxes: PdfBox[] };

const cases: CaseDefinition[] = [
  {
    label: "T1",
    expectedText: "A rock outcrop called Link shows signs of being formed by the deposition of water.",
    boxes: [[46.08, 217.376, 282.285, 227.6], [46.08, 229.376, 74.692, 239.6]],
  },
  {
    label: "T2",
    expectedText: "The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.",
    boxes: [[44.5, 584.66, 274.202, 596.48], [44.5, 600.16, 286.421, 611.98], [44.5, 615.66, 133.78, 627.48]],
  },
  {
    label: "T3",
    expectedText: "first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,",
    boxes: [[314.64, 139.4, 559.882, 151.22], [314.64, 154.9, 547.671, 166.72], [314.64, 170.4, 560.84, 182.22], [314.64, 185.9, 423.73, 197.72]],
  },
];

const questionLabels = new Map([
  [LINK_QUESTION, "The Link outcrop shows signs of formation by water deposition"],
  [POWER_QUESTION, "110 watts of electrical power"],
  [CRATER_QUESTION, "Gale Crater"],
]);

const measurements: Array<Record<string, unknown>> = [];

test.beforeEach(async ({ page, baseURL }) => {
  await page.addInitScript(() => sessionStorage.clear());
  await installLoopbackGuard(page, baseURL!);
});

test.beforeAll(async ({ browser }) => {
  console.log(`PLAYWRIGHT_BROWSER ${JSON.stringify({ name: browser.browserType().name(), version: browser.version() })}`);
});

test.afterAll(async () => {
  const target = path.resolve("test-results/nasa-pdfjs-highlight-measurements.json");
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify({ sourceSha256: SAMPLE_SHA, measurements }, null, 2)}\n`, "utf8");
  console.log(`NASA_PDFJS_MEASUREMENTS ${JSON.stringify({ sourceSha256: SAMPLE_SHA, measurements })}`);
});

async function installLoopbackGuard(page: Page, baseURL: string): Promise<void> {
  const origin = new URL(baseURL).origin;
  const outside: string[] = [];
  (page as Page & { __outsideRequests?: string[] }).__outsideRequests = outside;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== origin) outside.push(url.origin);
  });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) {
      outside.push(url.origin);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
}

async function assertLoopbackOnly(page: Page): Promise<void> {
  const outside = (page as Page & { __outsideRequests?: string[] }).__outsideRequests ?? [];
  expect(outside, `unexpected browser network origins: ${outside.join(", ")}`).toEqual([]);
}

async function openDesk(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByTestId("sample-pdf-preview")).toBeVisible();
  await expect(page.getByTestId("sample-pdf-mark")).toHaveCount(1);
  await expect(page.getByRole("button", { name: LINK_QUESTION })).toBeVisible();
  await expect(page.getByText("Model ready", { exact: true })).toHaveCount(0);
  const source = await page.getByTestId("sample-pdf-preview").getAttribute("data-testid");
  expect(source).toBe("sample-pdf-preview");
}

async function ask(page: Page, question: string): Promise<void> {
  await page.getByRole("button", { name: question }).click();
  await expect(page.locator("#chat-question")).toHaveValue(question);
  await expect(page.getByRole("button", { name: "Send message" })).toBeEnabled();
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".chat-message--assistant .chat-message__text")).toContainText(questionLabels.get(question)!);
}

async function openCitation(page: Page, label: string, pageNumber: number): Promise<void> {
  const citation = page.locator(".chat-citation").filter({ hasText: `[${label}]` });
  await expect(citation).toHaveCount(1);
  await citation.click();
  const popover = page.getByLabel(`Source ${label}`);
  await expect(popover).toBeVisible();
  await popover.getByRole("button", { name: `Open page ${pageNumber}` }).click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toBeVisible();
  const frame = page.getByTestId("pdf-page-frame");
  await expect(frame).toHaveAttribute("data-document-id", DOCUMENT_ID);
  await expect(frame).toHaveAttribute("data-manifest-hash", MANIFEST_HASH);
  await expect(frame).toHaveAttribute("data-page-number", String(pageNumber));
}

async function setScale(page: Page, requested: number): Promise<number> {
  const frame = page.getByTestId("pdf-page-frame");
  await expect(frame).toHaveAttribute("data-viewport-scale", /\d/);
  for (let step = 0; step < 20; step += 1) {
    await expect(frame).toHaveAttribute("data-viewport-scale", /\d/);
    const scale = Number(await frame.getAttribute("data-viewport-scale"));
    if (Math.abs(scale - requested) <= 0.055) return scale;
    const button = page.getByRole("button", { name: scale < requested ? "Zoom in" : "Zoom out" });
    await button.click();
    await expect.poll(async () => Number(await frame.getAttribute("data-viewport-scale")), { timeout: 10_000 })
      .not.toBe(scale);
    await expect(page.getByTestId("pdf-evidence-mark").first()).toBeVisible();
  }
  throw new Error(`Could not set PDF.js viewport near ${requested * 100}%`);
}

function rectArea(rect: { left: number; top: number; width: number; height: number }): number {
  return Math.max(0, rect.width) * Math.max(0, rect.height);
}

function intersection(a: { left: number; top: number; width: number; height: number }, b: { left: number; top: number; width: number; height: number }): number {
  const width = Math.max(0, Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left));
  const height = Math.max(0, Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top));
  return width * height;
}

async function measureSource(page: Page, target: CaseDefinition, requestedZoom: number, movementPhase = "forward"): Promise<Record<string, unknown>> {
  const actualZoom = await setScale(page, requestedZoom);
  const frame = page.getByTestId("pdf-page-frame");
  const measured = await frame.evaluate((node) => {
    const frameRect = node.getBoundingClientRect();
    const marks = Array.from(node.querySelectorAll<HTMLElement>("[data-testid='pdf-evidence-mark']"))
      .map((mark) => {
        const rect = mark.getBoundingClientRect();
        return {
          label: mark.dataset.sourceLabel ?? "",
          left: rect.left - frameRect.left,
          top: rect.top - frameRect.top,
          width: rect.width,
          height: rect.height,
        };
      });
    const textLines = Array.from(node.querySelectorAll<HTMLElement>(".textLayer span"))
      .map((span) => {
        const rect = span.getBoundingClientRect();
        return {
          text: span.textContent ?? "",
          left: rect.left - frameRect.left,
          top: rect.top - frameRect.top,
          width: rect.width,
          height: rect.height,
        };
      }).filter((line) => line.text.trim() && line.width > 0 && line.height > 0);
    return {
      page: Number(node.getAttribute("data-page-number")),
      rotation: Number(node.getAttribute("data-rotation")),
      scale: Number(node.getAttribute("data-viewport-scale")),
      width: frameRect.width,
      height: frameRect.height,
      marks,
      textLines,
    };
  });

  expect(measured.page).toBe(2);
  expect(measured.rotation).toBe(0);
  expect(measured.scale).toBeCloseTo(actualZoom, 4);
  expect(measured.width / measured.height).toBeCloseTo(612 / 792, 2);
  expect(measured.marks.length).toBeGreaterThanOrEqual(target.boxes.length);
  expect(measured.marks.every((mark) => mark.label === target.label)).toBe(true);

  // The independent boxes are top-left PDF points from CREDITS.md, transformed
  // by the actual PDF.js viewport scale (the NASA pages have 0-degree rotation).
  const expected = target.boxes.map(([x0, y0, x1, y1]) => ({
    left: x0 * actualZoom,
    top: y0 * actualZoom,
    width: (x1 - x0) * actualZoom,
    height: (y1 - y0) * actualZoom,
  }));
  const lineResults = expected.map((box) => {
    const intersectionArea = measured.marks.reduce((sum, mark) => sum + intersection(mark, box), 0);
    return { expected: box, overlapFraction: Math.min(1, intersectionArea / rectArea(box)) };
  });
  const sourceLineMatches = expected.map((box) => {
    const candidates = measured.textLines.map((line) => ({
      text: line.text,
      rect: line,
      overlapFraction: intersection(line, box) / rectArea(box),
    }));
    return candidates.sort((a, b) => b.overlapFraction - a.overlapFraction)[0] ?? {
      text: "",
      rect: { left: 0, top: 0, width: 0, height: 0 },
      overlapFraction: 0,
    };
  });
  const unrelatedLineIntersections = measured.marks.flatMap((mark) => measured.textLines
    .filter((line) => {
      const isTargetLine = expected.some((box) => intersection(line, box) / rectArea(box) >= 0.5);
      return !isTargetLine && intersection(mark, line) > 0;
    })
    .map((line) => ({ highlight: mark, unrelatedText: line.text, lineTop: line.top })));

  const result = {
    label: target.label,
    expectedText: target.expectedText,
    documentSha256: SAMPLE_SHA,
    documentId: DOCUMENT_ID,
    page: measured.page,
    requestedZoom,
    actualZoom,
    movementPhase,
    rotation: measured.rotation,
    deviceScaleFactor: 2,
    expectedLineRectangles: expected,
    renderedHighlightRectangles: measured.marks,
    sourceLineMatches: sourceLineMatches.map(({ text, rect, overlapFraction }) => ({ text, rect, overlapFraction })),
    overlapFractionPerLine: lineResults.map((line) => line.overlapFraction),
    unrelatedLineIntersections,
    passed: lineResults.every((line) => line.overlapFraction >= 0.9) &&
      sourceLineMatches.every((line) => line.overlapFraction >= 0.5) && unrelatedLineIntersections.length === 0,
  };
  measurements.push(result);
  for (const line of lineResults) expect(line.overlapFraction, JSON.stringify(result)).toBeGreaterThanOrEqual(0.9);
  for (const line of sourceLineMatches) expect(line.overlapFraction, JSON.stringify(result)).toBeGreaterThanOrEqual(0.5);
  expect(unrelatedLineIntersections, JSON.stringify(result)).toEqual([]);
  console.log(`NASA_CASE ${JSON.stringify(result)}`);
  return result;
}

test("three NASA passages meet per-line PDF.js overlap at 100% and 150%", async ({ page }) => {
  await openDesk(page);
  await ask(page, LINK_QUESTION);
  await expect(page.locator(".chat-answer-figure img")).toBeVisible();
  await expect(page.locator(".chat-answer-figure figcaption")).toContainText("NASA");
  const originalAnswer = await page.locator(".chat-message--assistant .chat-message__text").innerText();

  for (const target of cases) {
    await openCitation(page, target.label, 2);
    await expect(page.getByTestId("pdf-evidence-mark").first()).toBeVisible();
    for (const zoom of [1, 1.5]) await measureSource(page, target, zoom);
    await measureSource(page, target, 1, "zoom-return");

    if (target.label === "T1") {
      await expect(page.getByTestId("show-matches")).toHaveAttribute("aria-pressed", "false");
      await page.getByTestId("show-matches").click();
      await expect.poll(() => page.getByTestId("pdf-retrieved-mark").count()).toBeGreaterThan(0);
      await expect(page.locator(".chat-message--assistant .chat-message__text")).toHaveText(originalAnswer);
      await page.getByTestId("show-matches").click();
    }
    await page.getByRole("button", { name: "Close PDF viewer" }).click();
    await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveCount(0);
  }

  await assertLoopbackOnly(page);
});

test("all three first-run questions reach their own source page and passage", async ({ page }) => {
  await openDesk(page);
  const flows = [
    { question: LINK_QUESTION, sources: [
      { citation: "T1", page: 2, phrase: "Link shows signs" },
      { citation: "T2", page: 2, phrase: "rounded pebbles" },
      { citation: "T3", page: 2, phrase: "John Klein" },
    ] },
    { question: POWER_QUESTION, sources: [
      { citation: "T1", page: 3, phrase: "110 watts" },
    ] },
    { question: CRATER_QUESTION, sources: [
      { citation: "T1", page: 1, phrase: "Gale Crater" },
      { citation: "T2", page: 2, phrase: "John Klein" },
    ] },
  ];
  for (const flow of flows) {
    if (flow !== flows[0]) {
      await page.getByRole("button", { name: "New chat" }).click();
    }
    await ask(page, flow.question);
    for (const source of flow.sources) {
      await openCitation(page, source.citation, source.page);
      await expect(page.getByTestId("pdf-evidence-mark").first()).toBeVisible();
      await expect(page.getByText(source.phrase, { exact: false }).last()).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveCount(0);
      await expect(page.locator(".chat-citation.is-active")).toBeFocused();
    }
  }
  await assertLoopbackOnly(page);
});

test("unlocatable text opens the requested page with an honest no-highlight fallback", async ({ page }) => {
  await page.route("**/api/v1/ask", async (route) => {
    const upstream = await route.fetch();
    const value = await upstream.json() as { text_evidence: Array<{ passage: string }> };
    value.text_evidence[0]!.passage = "A passage that is not present anywhere in this PDF.";
    await route.fulfill({ response: upstream, json: value });
  });
  await openDesk(page);
  await ask(page, LINK_QUESTION);
  await openCitation(page, "T1", 2);
  await expect(page.locator(".pdf-viewer-context [role='status']")).toContainText("Passage on page 2");
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  await assertLoopbackOnly(page);
});

test("an incomplete rendered text fragment invalidates the entire source highlight", async ({ page }) => {
  await page.addInitScript(() => {
    const nativeGetClientRects = Range.prototype.getClientRects;
    Range.prototype.getClientRects = function getClientRectsWithMissingSourceFragment() {
      if (this.toString().trim() === "of water.") {
        (window as Window & { __suppressedSourceFragmentGeometry?: boolean }).__suppressedSourceFragmentGeometry = true;
        return [] as unknown as DOMRectList;
      }
      return nativeGetClientRects.call(this);
    };
  });
  await openDesk(page);
  await ask(page, LINK_QUESTION);
  await openCitation(page, "T1", 2);
  await expect.poll(() => page.evaluate(() =>
    (window as Window & { __suppressedSourceFragmentGeometry?: boolean }).__suppressedSourceFragmentGeometry ?? false,
  )).toBe(true);
  await expect(page.locator(".pdf-viewer-context [role='status']")).toContainText("Passage on page 2");
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  await assertLoopbackOnly(page);
});

test("text found on another page is not highlighted on the cited page", async ({ page }) => {
  await page.route("**/api/v1/ask", async (route) => {
    const upstream = await route.fetch();
    const value = await upstream.json() as { text_evidence: Array<{ passage: string }> };
    value.text_evidence[0]!.passage = "Curiosity landed successfully on the floor of Gale Crater";
    await route.fulfill({ response: upstream, json: value });
  });
  await openDesk(page);
  await ask(page, LINK_QUESTION);
  await openCitation(page, "T1", 2);
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "2");
  await expect(page.locator(".pdf-viewer-context [role='status']")).toContainText("Passage on page 2");
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  await assertLoopbackOnly(page);
});

test("mismatched citation identity cannot borrow the displayed NASA page", async ({ page }) => {
  const otherDocumentId = `doc_${"1".repeat(64)}`;
  await page.route("**/api/v1/ask", async (route) => {
    const upstream = await route.fetch();
    const value = await upstream.json() as { text_evidence: Array<{ document_id: string }> };
    value.text_evidence[0]!.document_id = otherDocumentId;
    await route.fulfill({ response: upstream, json: value });
  });
  await openDesk(page);
  await ask(page, LINK_QUESTION);
  const citation = page.locator(".chat-citation").filter({ hasText: "[T1]" });
  await citation.click();
  await page.getByLabel("Source T1").getByRole("button", { name: "Open page 2" }).click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("The original PDF is no longer available in this source collection.");
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  await assertLoopbackOnly(page);
});

test("citation popovers are click-only, dismiss with Escape and restore focus", async ({ page }) => {
  await openDesk(page);
  await ask(page, LINK_QUESTION);
  const citation = page.locator(".chat-citation").filter({ hasText: "[T1]" });
  await citation.hover();
  await expect(page.getByLabel("Source T1")).toHaveCount(0);
  await citation.click();
  await expect(page.getByLabel("Source T1")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("Source T1")).toHaveCount(0);
  await expect(citation).toBeFocused();
  await assertLoopbackOnly(page);
});

test("reduced motion keeps the citation-to-page interaction available", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/api/v1/ask", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.continue();
  });
  await openDesk(page);
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  await page.getByRole("button", { name: LINK_QUESTION }).click();
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.locator(".chat-thinking")).toBeVisible();
  const duration = await page.locator(".chat-thinking__dots i").first().evaluate((node) =>
    Number.parseFloat(getComputedStyle(node).animationDuration),
  );
  expect(duration, "reduced motion should remove visible waiting animation").toBeLessThan(0.001);
  await expect(page.locator(".chat-message--assistant .chat-message__text")).toContainText("water deposition");
  await openCitation(page, "T1", 2);
  await expect(page.getByTestId("pdf-evidence-mark").first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveCount(0);
  await expect(page.locator(".chat-citation.is-active")).toBeFocused();
  await assertLoopbackOnly(page);
});

test("desktop first-run alignment and citation popovers stay clear of reading content", async ({ page }) => {
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 768 });
    await openDesk(page);
    if (width === 1440) {
      const alignment = await page.evaluate(() => {
        const controls = document.querySelector(".reading-first-run__controls")!.getBoundingClientRect();
        const page = document.querySelector(".sample-pdf-preview__page")!.getBoundingClientRect();
        return Math.abs((controls.top + controls.height / 2) - (page.top + page.height / 2));
      });
      console.log("FIRST_RUN_ALIGNMENT", JSON.stringify({ width, centerDeltaPx: alignment }));
      expect(alignment, "first-run controls should align to the sample page center").toBeLessThanOrEqual(24);
    }
    await ask(page, LINK_QUESTION);
    for (const label of ["T1", "T2", "T3"]) {
      const citation = page.locator(".chat-citation").filter({ hasText: `[${label}]` });
      await citation.click();
      const measurements = await page.evaluate((citationId) => {
        const chip = document.getElementById(citationId)!;
        const paragraph = chip.closest("p")!;
        const previous = chip.previousElementSibling;
        const popover = document.querySelector<HTMLElement>(".chat-citation-popover")!;
        const userBubble = document.querySelector<HTMLElement>(".chat-message--user .chat-message__user-text")!;
        const conversation = document.querySelector<HTMLElement>(".reading-conversation")!;
        const range = document.createRange();
        if (previous) range.setStartBefore(previous);
        else range.setStart(paragraph, 0);
        range.setEndAfter(chip);
        const rects = Array.from(range.getClientRects()).map(({ left, top, width, height }) => ({ left, top, width, height }));
        const popoverRect = popover.getBoundingClientRect();
        const bubbleRect = userBubble.getBoundingClientRect();
        const paragraphRect = paragraph.getBoundingClientRect();
        const conversationRect = conversation.getBoundingClientRect();
        const overlap = (a: DOMRect, b: DOMRect) =>
          Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) *
          Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        const popoverDomRect = new DOMRect(popoverRect.x, popoverRect.y, popoverRect.width, popoverRect.height);
        const bubbleDomRect = new DOMRect(bubbleRect.x, bubbleRect.y, bubbleRect.width, bubbleRect.height);
        const sentenceOverlapArea = rects.reduce((total, rect) => total + overlap(new DOMRect(rect.left, rect.top, rect.width, rect.height), popoverDomRect), 0);
        return {
          sentenceOverlapArea,
          userBubbleOverlapArea: overlap(bubbleDomRect, popoverDomRect),
          popoverInsideConversation: popoverRect.left >= conversationRect.left && popoverRect.right <= conversationRect.right,
          popoverBelowParagraph: popoverRect.top >= paragraphRect.bottom - 1 && popoverRect.top - paragraphRect.bottom <= 40,
          popoverBottom: popoverRect.bottom,
          viewportHeight: window.innerHeight,
        };
      }, await citation.getAttribute("id") ?? "");
      console.log("CITATION_POPOVER_GEOMETRY", JSON.stringify({ width, label, ...measurements }));
      expect(measurements.sentenceOverlapArea, `${width}px ${label} cited sentence overlap`).toBe(0);
      expect(measurements.userBubbleOverlapArea, `${width}px ${label} user bubble overlap`).toBe(0);
      expect(measurements.popoverInsideConversation, `${width}px ${label} popover bounds`).toBe(true);
      expect(measurements.popoverBelowParagraph, `${width}px ${label} placement`).toBe(true);
      expect(measurements.popoverBottom, `${width}px ${label} visible viewport placement`).toBeLessThanOrEqual(measurements.viewportHeight);
      await page.keyboard.press("Escape");
      await expect(citation).toBeFocused();
    }
    if (width === 1440) {
      await openCitation(page, "T1", 2);
      const separation = await page.evaluate(() => {
        const bubble = document.querySelector<HTMLElement>(".chat-message--user .chat-message__user-text")!.getBoundingClientRect();
        const viewer = document.querySelector<HTMLElement>(".pdf-viewer")!.getBoundingClientRect();
        return viewer.left - bubble.right;
      });
      console.log("PDF_SHEET_CLEARANCE", JSON.stringify({ width, clearancePx: separation }));
      expect(separation, "desktop user bubble to PDF sheet clearance").toBeGreaterThanOrEqual(36);
      await page.getByRole("button", { name: "Close PDF viewer" }).click();
    }
  }
  await assertLoopbackOnly(page);
});

test("mobile citation remains visible above the bottom sheet and snap behavior stays usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDesk(page);
  const pageBox = await page.getByTestId("sample-pdf-preview").boundingBox();
  const composerBox = await page.locator(".reading-first-run .chat-composer").boundingBox();
  expect(pageBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(pageBox!.y + pageBox!.height).toBeLessThan(composerBox!.y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await ask(page, LINK_QUESTION);
  const citation = page.locator(".chat-citation").filter({ hasText: "[T2]" });
  await citation.click();
  await page.getByLabel("Source T2").getByRole("button", { name: "Open page 2" }).click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveAttribute("data-mobile-snap", "30");
  const citeRect = await citation.boundingBox();
  const sheetRect = await page.getByRole("dialog", { name: "PDF viewer" }).boundingBox();
  expect(citeRect).not.toBeNull();
  expect(sheetRect).not.toBeNull();
  expect(citeRect!.y + citeRect!.height).toBeLessThan(sheetRect!.y);
  await expect(page.locator(".reading-desk--viewer-open .chat-composer")).toBeVisible();

  const snapControl = page.getByRole("button", { name: /PDF sheet 30 percent/ });
  const toolbar = page.getByRole("navigation", { name: "PDF controls" });
  await expect(toolbar).toBeVisible();
  const controlOverlap = await snapControl.evaluate((button) => {
    const nav = document.querySelector<HTMLElement>(".pdf-viewer__controls");
    if (!nav) throw new Error("PDF controls toolbar is missing");
    const a = button.getBoundingClientRect();
    const b = nav.getBoundingClientRect();
    return Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) *
      Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  });
  expect(controlOverlap, "floating toolbar must not cover the mobile sheet-height control").toBe(0);
  await snapControl.click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveAttribute("data-mobile-snap", "55");
  await expect(page.locator(".reading-desk--viewer-open .chat-composer")).toBeHidden();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveAttribute("aria-modal", "true");
  await page.getByRole("button", { name: /PDF sheet 55 percent/ }).click();
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveAttribute("data-mobile-snap", "92");
  await expect(page.getByTestId("pdf-evidence-mark").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toHaveCount(0);
  await expect(citation).toBeFocused();
  await assertLoopbackOnly(page);
});

test("a real retry request reports model reachability without a ready claim", async ({ page }) => {
  let attempts = 0;
  let allowRecovery = false;
  await page.route("**/api/v1/chat/models", async (route) => {
    attempts += 1;
    if (!allowRecovery) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "ollama_unavailable" } }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      availability: "available", configured_model: "gemma4:e2b", configured_model_available: true,
      available_models: ["gemma4:e2b"], error_code: null,
    }) });
  });
  await openDesk(page);
  const failure = "Can't reach the model. Start Ollama, then check again.";
  await expect(page.getByRole("status").filter({ hasText: failure })).toBeVisible();
  allowRecovery = true;
  await page.getByRole("button", { name: "Check again" }).click();
  await expect(page.getByRole("status").filter({ hasText: failure })).toHaveCount(0);
  expect(attempts).toBeGreaterThanOrEqual(2);
  await expect(page.getByText("Model ready", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("sample-pdf-mark")).toHaveCount(1);
  await assertLoopbackOnly(page);
});

test("first-run sample checksum matches the original tracked NASA PDF", async ({ page }) => {
  await openDesk(page);
  const response = await page.request.get("/reading-desk/mars-science-laboratory.pdf");
  expect(response.ok()).toBe(true);
  const digest = createHash("sha256").update(await response.body()).digest("hex");
  expect(digest).toBe(SAMPLE_SHA);
  await assertLoopbackOnly(page);
});
