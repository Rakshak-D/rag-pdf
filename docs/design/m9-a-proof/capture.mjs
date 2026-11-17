import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';

const require = createRequire(path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@playwright', 'cli', 'package.json'));
const { chromium } = require('playwright');
const proofRoot = path.resolve('docs/design/m9-a-proof');
const shotRoot = path.join(proofRoot, 'shots');
const base = 'http://127.0.0.1:8765';
const entry = '/proof-static-not-functional.html';
const samplePath = path.resolve('data/pdf/mars-science-laboratory.pdf');
await fs.mkdir(shotRoot, { recursive: true });

const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const waitForRender = async (page) => page.evaluate(async () => {
  await document.fonts.ready;
  await Promise.all(Array.from(document.images).map((image) => image.decode().catch(() => undefined)));
});
const capture = async ({ url, file, width, height, action, fullPage = false, reducedMotion = 'reduce' }) => {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, reducedMotion });
  const page = await context.newPage();
  await page.goto(base + url, { waitUntil: 'networkidle' });
  await waitForRender(page);
  if (action) await action(page);
  await page.screenshot({ path: path.join(shotRoot, file), fullPage, animations: 'disabled' });
  console.log('CAPTURED ' + file + ' ' + width + 'x' + height + (fullPage ? ' full-page' : ''));
  await context.close();
};

try {
  await capture({ url: entry + '?state=first-run', file: 'r5-first-run-1440.png', width: 1440, height: 900 });
  await capture({ url: entry + '?state=first-run', file: 'r5-first-run-390.png', width: 390, height: 844 });
  for (const [width, height, file] of [[1440, 900, 'r5-citation-popover-1440.png'], [1024, 768, 'r5-citation-popover-1024.png']]) {
    await capture({
      url: entry + '?state=answered-closed', file, width, height,
      action: async (page) => {
        await page.locator('.citation[data-source-id="link-caption"]').first().hover();
        await page.locator('.citation-popover.is-visible').waitFor({ state: 'visible' });
      }
    });
  }
  await capture({ url: entry + '?state=answer-shapes', file: 'r5-answer-shapes.png', width: 1440, height: 900, fullPage: true });
  for (const snap of [30, 55, 92]) {
    await capture({
      url: entry + '?state=answered-closed', file: `r5-mobile-sheet-${snap}.png`, width: 390, height: 844,
      action: async (page) => {
        await page.locator('.citation[data-source-id="john-klein-water"]').first().click();
        await page.waitForFunction(() => document.body.dataset.pdfOpen === 'true');
        const snapOrder = [30, 55, 92];
        for (let tries = 0; tries < 3; tries += 1) {
          const current = Number(await page.locator('body').getAttribute('data-sheet-snap'));
          if (current === snap) break;
          const next = snapOrder[(snapOrder.indexOf(current) + 1) % snapOrder.length];
          await page.locator('#sheet-grip').click();
          await page.waitForFunction((value) => document.body.dataset.sheetSnap === String(value), next);
        }
        if (Number(await page.locator('body').getAttribute('data-sheet-snap')) !== snap) {
          throw new Error(`Could not reach ${snap}% mobile sheet snap`);
        }
        await page.waitForTimeout(140);
      }
    });
  }
  await capture({ url: entry + '?state=model-unreachable&open=1', file: 'r5-model-unreachable-1440.png', width: 1440, height: 900 });
  await capture({ url: '/logo-comparison.html', file: 'r5-logo-comparison.png', width: 1440, height: 900, fullPage: true });
  console.log('BROWSER ' + browser.version() + ' Chrome channel');
} finally {
  await browser.close();
}
