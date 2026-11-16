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
  await capture({ url: entry + '?state=first-run', file: 'r4-first-run-1440.png', width: 1440, height: 900 });
  await capture({ url: entry + '?state=first-run', file: 'r4-first-run-390.png', width: 390, height: 844 });
  await capture({ url: entry + '?state=answered-closed', file: 'r4-answered-closed-1440.png', width: 1440, height: 900 });
  await capture({ url: entry + '?state=answered-open', file: 'r4-answered-open-1440.png', width: 1440, height: 900 });
  await capture({ url: entry + '?state=answered-matches', file: 'r4-matches-1440.png', width: 1440, height: 900 });
  await capture({
    url: entry + '?state=answered-closed', file: 'r4-mobile-sheet-55-390.png', width: 390, height: 844,
    action: async (page) => {
      await page.locator('.citation').first().click();
      await page.locator('#pdf-sheet.is-open').waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.body.dataset.sheetSnap === '92');
      await page.locator('#sheet-grip').click();
      await page.locator('#sheet-grip').click();
      await page.waitForFunction(() => document.body.dataset.sheetSnap === '55');
      await page.waitForTimeout(80);
    }
  });
  await capture({
    url: entry + '?state=answered-closed', file: 'r4-mobile-answered-390.png', width: 390, height: 844,
    action: async (page) => {
      await page.evaluate(() => { document.getElementById('app').scrollTop = 0; });
      await page.waitForTimeout(30);
    }
  });
  await capture({ url: entry + '?state=model-unreachable&open=1', file: 'r4-model-unreachable-1440.png', width: 1440, height: 900 });
  await capture({ url: entry + '?state=answer-shapes', file: 'r4-answer-shapes-1440.png', width: 1440, height: 900, fullPage: true });
  await capture({
    url: entry + '?state=answered-closed', file: 'r4-citation-popover-1440.png', width: 1440, height: 900,
    action: async (page) => {
      await page.locator('.citation').first().hover();
      await page.locator('.citation-popover.is-visible').waitFor({ state: 'visible' });
    }
  });
  await capture({ url: '/logo-comparison.html', file: 'r4-logo-comparison.png', width: 1440, height: 900, fullPage: true });
  await capture({
    url: entry + '?state=first-run', file: 'r4-reading-progress-1440.png', width: 1440, height: 900, reducedMotion: 'no-preference',
    action: async (page) => {
      await page.locator('.document-open').click();
      await page.locator('#pdf-sheet.is-open').waitFor({ state: 'visible' });
      await page.locator('#next-page').click();
      await page.locator('#next-page').click();
      await page.waitForFunction(() => document.querySelector('#toolbar-page').textContent === '3 / 4');
      await page.waitForTimeout(250);
    }
  });
  console.log('BROWSER ' + browser.version() + ' Chrome channel');
} finally {
  await browser.close();
}
