import { createHash } from "node:crypto";
import { expect, test, type APIRequestContext } from "@playwright/test";

const digest = "65ba3e145dcb1ab75a6d89717bc46791fda88b1957b4d63c258c044f5ad7a77c";
const readOnlyMethods = new Set(["GET", "HEAD", "OPTIONS"]);

async function checkedApiRead(
  context: APIRequestContext,
  method: string,
  url: string,
  origin: string,
  observed: Array<{ method: string; origin: string; path: string }>,
) {
  const target = new URL(url);
  expect(readOnlyMethods.has(method)).toBe(true);
  expect(target.origin).toBe(origin);
  observed.push({ method, origin: target.origin, path: target.pathname });
  const response = await context.fetch(target.toString(), { method, maxRedirects: 0 });
  expect(new URL(response.url()).origin).toBe(origin);
  return response;
}

test("the real launcher serves the isolated PDF through the Library", async ({ page, request, baseURL }) => {
  const external: string[] = [];
  const authenticated: string[] = [];
  const stateChanging: string[] = [];
  const apiObserved: Array<{ method: string; origin: string; path: string }> = [];
  const origin = new URL(baseURL!).origin;
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) { external.push(url.origin); await route.abort(); return; }
    if ((await request.allHeaders()).authorization) authenticated.push(url.pathname);
    if (!readOnlyMethods.has(request.method().toUpperCase())) {
      stateChanging.push(request.method().toUpperCase());
      await route.abort();
      return;
    }
    await route.continue();
  });
  await page.goto("/");
  const selection = await checkedApiRead(
    request,
    "GET",
    `${origin}/api/v1/index/selection`,
    origin,
    apiObserved,
  );
  expect(selection.status()).toBe(200);
  expect((await selection.json()).selection_status).toBe("unselected");
  await page.getByRole("button", { name: "Menu" }).click();
  await page.getByRole("navigation", { name: "Reading Desk menu" }).getByRole("link", { name: "Library" }).click();
  await expect(page.getByText("Orbit_Field_Notes.pdf", { exact: true })).toBeVisible();
  const response = await checkedApiRead(
    request,
    "GET",
    `${origin}/bootstrap-api/v1/bootstrap/documents?limit=50&offset=0`,
    origin,
    apiObserved,
  );
  expect(response.status()).toBe(200);
  const inventory = await response.json();
  expect(inventory.total).toBe(1);
  const doc = inventory.documents[0];
  expect(doc.sha256).toBe(digest);
  expect(doc.document_id).toBe(`doc_${createHash("sha256").update(`${doc.relative_path}\0${digest}`).digest("hex")}`);
  const pdf = page.waitForResponse(r => r.url().includes(`/documents/${doc.document_id}/content?`));
  await page.getByRole("link", { name: "Preview" }).click();
  const pdfResponse = await pdf;
  expect(pdfResponse.status()).toBe(200);
  const headers = await pdfResponse.allHeaders();
  expect(headers["x-rag-document-id"]).toBe(doc.document_id);
  expect(headers["x-rag-document-sha256"]).toBe(digest);
  expect(headers["x-rag-manifest-hash"]).toBe(inventory.manifest_hash);
  expect(headers["x-rag-page-count"]).toBe("3");
  expect(createHash("sha256").update(await pdfResponse.body()).digest("hex")).toBe(digest);
  await expect(page.getByRole("dialog", { name: "PDF viewer" })).toBeVisible();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "1");
  await expect(page.locator(".textLayer")).toContainText("184");
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByTestId("pdf-page-frame")).toHaveAttribute("data-page-number", "2");
  await expect(page.locator(".textLayer")).toContainText("Juniper-47");
  await page.goto(`/documents/view/doc_${"f".repeat(64)}?manifest_hash=${inventory.manifest_hash}&page=1`);
  await expect(page.getByText("The original PDF is no longer available in this source collection.", { exact: true })).toBeVisible();
  expect(external).toEqual([]);
  expect(authenticated).toEqual([]);
  expect(stateChanging).toEqual([]);
  expect(apiObserved.map(({ method }) => method)).toEqual(["GET", "GET"]);
  expect(apiObserved.every(({ origin: requestOrigin }) => requestOrigin === origin)).toBe(true);
  expect(apiObserved.map(({ path }) => path)).toEqual([
    "/api/v1/index/selection",
    "/bootstrap-api/v1/bootstrap/documents",
  ]);
  console.log("HOSTED_BROWSER_PASS", JSON.stringify({
    sha256: digest,
    pages: 3,
    browserExternalRequests: external.length,
    browserAuthorizationHeaders: authenticated.length,
    browserStateChangingMethods: stateChanging.length,
    separateApiRequests: apiObserved,
    indexSelection: "unselected",
    buildSubmitted: false,
  }));
});
