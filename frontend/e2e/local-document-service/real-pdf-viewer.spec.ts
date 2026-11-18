import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const NASA_SHA256 = "2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6";
const LINK_PASSAGE = "A rock outcrop called Link shows signs of being formed by the deposition of water.";

type Inventory = {
  inventory_status: string;
  documents: Array<{ document_id: string; relative_path: string; size_bytes: number; sha256: string }>;
  manifest_hash: string;
  total: number;
};

async function installLoopbackOnly(page: Page, baseURL: string): Promise<string[]> {
  const appOrigin = new URL(baseURL).origin;
  const externalOrigins: string[] = [];
  page.on("request", async (request) => {
    const url = new URL(request.url());
    if (url.origin !== appOrigin) externalOrigins.push(url.origin);
    if (url.pathname.startsWith("/bootstrap-api/")) {
      expect((await request.headers()).authorization).toBeUndefined();
    }
  });
  await page.route("**/*", async (route) => {
    const origin = new URL(route.request().url()).origin;
    if (origin !== appOrigin) {
      externalOrigins.push(origin);
      await route.abort("blockedbyclient");
      return;
    }
    await route.continue();
  });
  return externalOrigins;
}

test("real local inventory opens authenticated NASA PDF bytes with correct identity and evidence", async ({ page, baseURL }) => {
  const externalOrigins = await installLoopbackOnly(page, baseURL!);
  const inventoryResponse = await page.request.get(`${baseURL}/bootstrap-api/v1/bootstrap/documents?limit=50&offset=0`);
  expect(inventoryResponse.status()).toBe(200);
  const inventory = await inventoryResponse.json() as Inventory;
  expect(inventory.inventory_status).toBe("available");
  expect(inventory.total).toBe(1);
  expect(inventory.documents).toHaveLength(1);
  const source = inventory.documents[0]!;
  expect(source.relative_path).toBe("mars-science-laboratory.pdf");
  expect(source.sha256).toBe(NASA_SHA256);
  const expectedDocumentId = `doc_${createHash("sha256").update(`${source.relative_path}\0${source.sha256}`).digest("hex")}`;
  expect(source.document_id).toBe(expectedDocumentId);

  const contentUrl = new URL(
    `/bootstrap-api/v1/bootstrap/documents/${source.document_id}/content?manifest_hash=${inventory.manifest_hash}`,
    baseURL,
  ).toString();
  let browserPdfSha256: string | null = null;
  await page.route(contentUrl, async (route) => {
    const response = await route.fetch();
    const bytes = await response.body();
    browserPdfSha256 = createHash("sha256").update(bytes).digest("hex");
    await route.fulfill({ response, body: bytes });
  });
  const contentResponsePromise = page.waitForResponse((response) => response.url() === contentUrl);
  await page.addInitScript((state) => {
    history.replaceState({ usr: state, key: "real-local-document", idx: 0 }, "", location.href);
  }, {
    returnTo: "/documents",
    citation: {
      label: "T1",
      page: 2,
      documentId: source.document_id,
      manifestHash: inventory.manifest_hash,
      passage: LINK_PASSAGE,
    },
  });
  await page.goto(`/documents/view/${source.document_id}?manifest_hash=${inventory.manifest_hash}&page=2`);
  const contentResponse = await contentResponsePromise;
  expect(contentResponse.status()).toBe(200);
  const headers = await contentResponse.allHeaders();
  expect(headers["content-type"]).toContain("application/pdf");
  expect(headers["x-rag-document-id"]).toBe(source.document_id);
  expect(headers["x-rag-document-sha256"]).toBe(NASA_SHA256);
  expect(headers["x-rag-manifest-hash"]).toBe(inventory.manifest_hash);
  expect(headers["x-rag-page-count"]).toBe("4");
  expect(browserPdfSha256).toBe(NASA_SHA256);

  await expect(page.getByRole("heading", { name: "mars-science-laboratory.pdf" })).toBeVisible();
  await expect(page.getByText("4 pages", { exact: true })).toBeVisible();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-document-id", source.document_id);
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "2");
  await expect(page.locator(".textLayer")).toContainText("A rock outcrop called Link");
  await expect(page.locator(".pdf-viewer-context [role='status']")).toHaveCount(0);
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(2);
  await expect(page.getByTestId("pdf-evidence-mark").first()).toHaveAttribute("data-source-label", "T1");
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toBeVisible();
  expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin);
  expect(externalOrigins).toEqual([]);
  expect(page.url()).not.toContain("token");
  const browserState = await page.evaluate(() => JSON.stringify({
    session: Object.fromEntries(Array.from({ length: sessionStorage.length }, (_, index) => {
      const key = sessionStorage.key(index) ?? "";
      return [key, sessionStorage.getItem(key)];
    })),
    local: Object.fromEntries(Array.from({ length: localStorage.length }, (_, index) => {
      const key = localStorage.key(index) ?? "";
      return [key, localStorage.getItem(key)];
    })),
  }));
  expect(browserState).not.toMatch(/Bearer\s|[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}/u);
  console.log(`REAL_LOCAL_PDF_VIEWER ${JSON.stringify({
    documentId: source.document_id,
    manifestHash: inventory.manifest_hash,
    sha256: NASA_SHA256,
    pageCount: 4,
    citedPage: 2,
    evidenceMarks: 2,
    browserCredentialHeader: "absent",
    externalOrigins,
  })}`);
});

test("a missing document identity shows the existing unavailable-source state", async ({ page, baseURL }) => {
  const externalOrigins = await installLoopbackOnly(page, baseURL!);
  const inventoryResponse = await page.request.get(`${baseURL}/bootstrap-api/v1/bootstrap/documents?limit=50&offset=0`);
  expect(inventoryResponse.status()).toBe(200);
  const inventory = await inventoryResponse.json() as Inventory;
  const missingId = `doc_${"f".repeat(64)}`;
  await page.addInitScript(() => sessionStorage.clear());
  await page.goto(`/documents/view/${missingId}?manifest_hash=${inventory.manifest_hash}&page=1`);
  await expect(page.getByText("The original PDF is no longer available in this source collection.", { exact: true })).toBeVisible();
  await expect(page.getByTestId("pdf-evidence-mark")).toHaveCount(0);
  expect(externalOrigins).toEqual([]);
});
