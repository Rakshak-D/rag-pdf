import { expect, test, type Page } from "@playwright/test";

type Rect = { left: number; top: number; width: number; height: number };
type LayoutCase = { page: number; passage: string; expected_lines: number[][]; expected_fallback?: boolean };
type LayoutDocument = {
  document_id: string;
  manifest_hash: string;
  relative_path: string;
  sha256: string;
  page_count: number;
  pages: Array<{ page: number; width: number; height: number; unrotated_width: number; unrotated_height: number; rotation: number }>;
  cases: Record<string, LayoutCase>;
};
type LayoutManifest = { generator: string; pymupdf_version: string; oracle: string; documents: LayoutDocument[] };

const measurements: Array<Record<string, unknown>> = [];

test.beforeEach(async ({ page, baseURL }) => {
  await page.addInitScript(() => sessionStorage.clear());
  const appOrigin = new URL(baseURL!).origin;
  const outside: string[] = [];
  (page as Page & { __outsideRequests?: string[] }).__outsideRequests = outside;
  page.on("request", (request) => {
    const origin = new URL(request.url()).origin;
    if (origin !== appOrigin) outside.push(origin);
  });
  await page.route("**/*", async (route) => {
    const origin = new URL(route.request().url()).origin;
    if (origin !== appOrigin) {
      outside.push(origin);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
});

test.afterAll(async () => {
  const { mkdir, writeFile } = await import("node:fs/promises");
  const path = await import("node:path");
  const target = path.resolve("test-results/pdfjs-layout-measurements.json");
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify({ measurements }, null, 2)}\n`, "utf8");
  console.log(`PDFJS_LAYOUT_MEASUREMENTS ${JSON.stringify(measurements)}`);
});

async function getFixture(page: Page): Promise<LayoutDocument> {
  const response = await page.request.get("/bootstrap-api/test-fixtures/layout-manifest");
  expect(response.ok()).toBe(true);
  const manifest = await response.json() as LayoutManifest;
  expect(manifest.pymupdf_version).toBe("1.26.7");
  expect(manifest.oracle).toContain("independent expected rectangles");
  expect(manifest.documents).toHaveLength(1);
  return manifest.documents[0]!;
}

async function openFixture(page: Page, document: LayoutDocument, sourceCase: LayoutCase): Promise<void> {
  const state = {
    returnTo: "/",
    citation: {
      label: "T1",
      page: sourceCase.page,
      documentId: document.document_id,
      manifestHash: document.manifest_hash,
      passage: sourceCase.passage,
    },
  };
  const url = `/documents/view/${document.document_id}?manifest_hash=${document.manifest_hash}&page=${sourceCase.page}`;
  await page.addInitScript((initialState) => {
    history.replaceState({ usr: initialState, key: "layout-fixture", idx: 0 }, "", location.href);
  }, state);
  await page.goto(url);
  const frame = page.getByTestId("pdf-page-frame");
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toBeVisible();
  await expect(frame).toHaveAttribute("data-document-id", document.document_id);
  await expect(frame).toHaveAttribute("data-manifest-hash", document.manifest_hash);
  await expect(frame).toHaveAttribute("data-page-number", String(sourceCase.page));
  await expect(frame).toHaveAttribute("data-rotation", /\d+/);
}

async function setScale(page: Page, requested: number): Promise<number> {
  const frame = page.getByTestId("pdf-page-frame");
  for (let step = 0; step < 35; step += 1) {
    const scale = Number(await frame.getAttribute("data-viewport-scale"));
    if (!Number.isFinite(scale)) {
      await expect.poll(async () => Number(await frame.getAttribute("data-viewport-scale")))
        .toBeGreaterThan(0);
      continue;
    }
    if (Number.isFinite(scale) && Math.abs(scale - requested) <= 0.055) return scale;
    const button = page.getByRole("button", { name: scale < requested ? "Zoom in" : "Zoom out" });
    await button.click();
    await expect.poll(async () => {
      const value = Number(await frame.getAttribute("data-viewport-scale"));
      return Number.isFinite(value) && Math.abs(value - scale) > 0.001 ? value : 0;
    }).toBeGreaterThan(0);
  }
  throw new Error(`PDF.js viewer did not reach the requested scale ${requested}.`);
}

function area(rect: Rect): number { return rect.width * rect.height; }
function intersection(a: Rect, b: Rect): number {
  return Math.max(0, Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left)) *
    Math.max(0, Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top));
}

function textRectInRenderedPage(rect: Rect, pageInfo: LayoutDocument["pages"][number], scale: number): Rect {
  if (pageInfo.rotation === 90) {
    return {
      left: pageInfo.unrotated_height * scale - (rect.top + rect.height),
      top: rect.left,
      width: rect.height,
      height: rect.width,
    };
  }
  if (pageInfo.rotation === 180) {
    return {
      left: pageInfo.unrotated_width * scale - (rect.left + rect.width),
      top: pageInfo.unrotated_height * scale - (rect.top + rect.height),
      width: rect.width,
      height: rect.height,
    };
  }
  if (pageInfo.rotation === 270) {
    return {
      left: rect.top,
      top: pageInfo.unrotated_width * scale - (rect.left + rect.width),
      width: rect.height,
      height: rect.width,
    };
  }
  return rect;
}

async function measure(page: Page, document: LayoutDocument, sourceCase: LayoutCase, requestedScale: number): Promise<void> {
  const actualScale = await setScale(page, requestedScale);
  const frame = page.getByTestId("pdf-page-frame");
  const actual = await frame.evaluate((node) => {
    const frameRect = node.getBoundingClientRect();
    const rectOf = (element: Element): Rect => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left - frameRect.left, top: rect.top - frameRect.top, width: rect.width, height: rect.height };
    };
    return {
      documentId: node.getAttribute("data-document-id"),
      page: Number(node.getAttribute("data-page-number")),
      scale: Number(node.getAttribute("data-viewport-scale")),
      rotation: Number(node.getAttribute("data-rotation")),
      width: frameRect.width,
      height: frameRect.height,
      marks: Array.from(node.querySelectorAll<HTMLElement>("[data-testid='pdf-evidence-mark']")).map(rectOf),
      textLines: Array.from(node.querySelectorAll<HTMLElement>(".textLayer span"))
        .filter((span) => (span.textContent ?? "").trim())
        .map((span) => ({ text: span.textContent ?? "", ...rectOf(span) })),
    };
  });
  const pageInfo = document.pages.find((entry) => entry.page === sourceCase.page)!;
  const renderedTextLines = actual.textLines.map((line) => ({
    ...textRectInRenderedPage(line, pageInfo, actualScale),
    text: line.text,
  }));
  const expected = sourceCase.expected_lines.map(([x0, y0, x1, y1]) => ({
    left: x0! * actualScale,
    top: y0! * actualScale,
    width: (x1! - x0!) * actualScale,
    height: (y1! - y0!) * actualScale,
  }));
  const overlaps = expected.map((line) => Math.min(1,
    actual.marks.reduce((sum, mark) => sum + intersection(mark, line), 0) / area(line)));
  const lineOwners = expected.map((line) => renderedTextLines
    .map((text) => ({ text: text.text, fraction: intersection(text, line) / area(line) }))
    .sort((left, right) => right.fraction - left.fraction)[0]);
  const unrelatedIntersections = actual.marks.flatMap((mark) => renderedTextLines
    .filter((line) => !expected.some((target) => intersection(line, target) / area(target) >= 0.5) && intersection(mark, line) > 0)
    .map((line) => ({ mark, text: line.text })));
  const result = {
    fixtureSha256: document.sha256,
    file: document.relative_path,
    casePassage: sourceCase.passage,
    page: actual.page,
    expectedPage: sourceCase.page,
    rotation: actual.rotation,
    expectedRotation: pageInfo.rotation,
    pageDimensions: { actual: [actual.width, actual.height], expected: [pageInfo.width * actualScale, pageInfo.height * actualScale] },
    requestedScale,
    actualScale,
    expectedLineRectangles: expected,
    renderedHighlightRectangles: actual.marks,
    overlapFractionPerLine: overlaps,
    matchedTextLines: lineOwners,
    unrelatedLineIntersections: unrelatedIntersections,
  };
  measurements.push(result);
  console.log(`PDFJS_LAYOUT_CASE ${JSON.stringify(result)}`);
  expect(actual.documentId).toBe(document.document_id);
  expect(actual.page).toBe(sourceCase.page);
  expect(actual.rotation).toBe(pageInfo.rotation);
  expect(actual.width / actual.height).toBeCloseTo(pageInfo.width / pageInfo.height, 2);
  expect(actual.marks.length).toBeGreaterThanOrEqual(expected.length);
  expect(overlaps, JSON.stringify(result)).toHaveLength(expected.length);
  for (const overlap of overlaps) expect(overlap, JSON.stringify(result)).toBeGreaterThanOrEqual(0.9);
  expect(lineOwners.every((owner) => owner && owner.fraction >= 0.5), JSON.stringify(result)).toBe(true);
  expect(unrelatedIntersections, JSON.stringify(result)).toEqual([]);
}

async function assertLoopbackOnly(page: Page, baseURL: string): Promise<void> {
  const outside = (page as Page & { __outsideRequests?: string[] }).__outsideRequests ?? [];
  expect(outside, `unexpected browser origins: ${outside.join(", ")}`).toEqual([]);
  expect(new URL(baseURL).hostname).toBe("127.0.0.1");
}

test("variable-size page and genuine multiline source geometry pass at two scales", async ({ page, baseURL }) => {
  const document = await getFixture(page);
  const sourceCase = document.cases.multiline!;
  await openFixture(page, document, sourceCase);
  for (const scale of [1, 1.5]) await measure(page, document, sourceCase, scale);
  await measure(page, document, sourceCase, 1);
  await assertLoopbackOnly(page, baseURL!);
});

test("a second target on the non-NASA page dimensions stays aligned at two scales", async ({ page, baseURL }) => {
  const document = await getFixture(page);
  const sourceCase = document.cases["variable-dimensions"]!;
  await openFixture(page, document, sourceCase);
  for (const scale of [1, 1.5]) await measure(page, document, sourceCase, scale);
  await assertLoopbackOnly(page, baseURL!);
});

test("90-degree rotated PDF.js page keeps source geometry at two scales", async ({ page, baseURL }) => {
  const document = await getFixture(page);
  const sourceCase = document.cases.rotated!;
  await openFixture(page, document, sourceCase);
  for (const scale of [1, 1.5]) await measure(page, document, sourceCase, scale);
  await measure(page, document, sourceCase, 1);
  await assertLoopbackOnly(page, baseURL!);
});

test("text assembled across separate PDF columns falls back without a highlight", async ({ page, baseURL }) => {
  const document = await getFixture(page);
  const sourceCase = document.cases["column-discontinuity"]!;
  await openFixture(page, document, sourceCase);
  await expect(page.locator(".pdf-viewer-context [role='status']")).toContainText("Passage on page 1");
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  const text = await page.locator(".textLayer").innerText();
  expect(text).toContain("A column concludes with source");
  expect(text).toContain("Second column has unrelated text");
  await assertLoopbackOnly(page, baseURL!);
});

test("page navigation clears the prior citation mark and renders the next page", async ({ page, baseURL }) => {
  const document = await getFixture(page);
  const sourceCase = document.cases.multiline!;
  await openFixture(page, document, sourceCase);
  await expect.poll(() => page.getByTestId("pdf-evidence-mark").count()).toBeGreaterThan(0);
  const previousCount = await page.getByTestId("pdf-evidence-mark").count();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "2");
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-rotation", "90");
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  await expect(page.locator(".textLayer")).toContainText("Rotated source passage");
  await page.getByRole("button", { name: "Previous page" }).click();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "1");
  await expect.poll(() => page.getByTestId("pdf-evidence-mark").count()).toBe(previousCount);
  await assertLoopbackOnly(page, baseURL!);
});
