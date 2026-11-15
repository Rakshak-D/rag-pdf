import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';

const require = createRequire(path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@playwright', 'cli', 'package.json'));
const { chromium } = require('playwright');
const root = path.resolve('docs/design/m9-a-proof');
const base = 'http://127.0.0.1:8765';
const entry = '/proof-static-not-functional.html';
const viewports = [{ w: 1440, h: 900 }, { w: 1024, h: 768 }, { w: 390, h: 844 }];
const states = ['first-run', 'answered-closed', 'answered-open', 'answered-matches', 'model-missing', 'reading-progress'];
const samplePath = path.resolve('data/pdf/mars-science-laboratory.pdf');
await fs.mkdir(path.join(root, 'shots'), { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const waitForAssets = async (page) => page.evaluate(async () => {
  await document.fonts.ready;
  await Promise.all([...document.images].map((image) => image.decode().catch(() => undefined)));
});
const capturePage = async ({ url, name, viewport, action, fullPage = false }) => {
  const context = await browser.newContext({ viewport: { width: viewport.w, height: viewport.h }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await page.goto(`${base}${url}`, { waitUntil: 'networkidle' });
  await waitForAssets(page);
  if (action) await action(page);
  await page.screenshot({ path: path.join(root, 'shots', name), fullPage, animations: 'disabled' });
  console.log(`CAPTURED ${name} ${viewport.w}x${viewport.h}`);
  await context.close();
};
try {
  for (const state of states) {
    for (const viewport of viewports) {
      await capturePage({
        url: `${entry}?state=${state}`,
        name: `${state}-${viewport.w}.png`,
        viewport,
        action: state === 'answered-open' || state === 'answered-matches' || state === 'reading-progress'
          ? async (page) => page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#pdf-sheet').classList.contains('is-open'))
          : undefined,
      });
    }
  }

  await capturePage({
    url: `${entry}?state=answered-closed`, name: 'citation-popover-desktop.png', viewport: { w: 1440, h: 900 },
    action: async (page) => {
      await page.locator('.citation-chip').first().hover();
      await page.locator('.citation-popover').first().waitFor({ state: 'visible' });
    },
  });
  await capturePage({
    url: `${entry}?state=answered-closed`, name: 'citation-popover-mobile.png', viewport: { w: 390, h: 844 },
    action: async (page) => {
      await page.locator('.citation-chip').first().focus();
      await page.locator('.citation-popover').first().waitFor({ state: 'visible' });
    },
  });
  await capturePage({
    url: `${entry}?state=model-missing&open=1`, name: 'model-missing-pdf-open.png', viewport: { w: 1440, h: 900 },
    action: async (page) => page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden),
  });
  await capturePage({ url: '/font-comparison.html', name: 'font-comparison.png', viewport: { w: 1440, h: 900 }, fullPage: true });
  await capturePage({ url: '/logo-comparison.html', name: 'logo-comparison.png', viewport: { w: 1440, h: 900 }, fullPage: true });
  await capturePage({
    url: `${entry}?state=first-run`, name: 'attachment-selected-1440.png', viewport: { w: 1440, h: 900 },
    action: async (page) => {
      await page.locator('#attach-button').click();
      await page.locator('#pdf-input').setInputFiles(samplePath);
      await page.locator('#attachment-row').waitFor({ state: 'visible' });
      await page.getByRole('button', { name: 'Open sample pages' }).waitFor({ state: 'visible' });
    },
  });
  await capturePage({
    url: `${entry}?state=first-run`, name: 'attachment-selected-390.png', viewport: { w: 390, h: 844 },
    action: async (page) => {
      await page.locator('#attach-button').click();
      await page.locator('#pdf-input').setInputFiles(samplePath);
      await page.getByRole('button', { name: 'Open sample pages' }).waitFor({ state: 'visible' });
    },
  });
  for (const viewport of [{ w: 1440, h: 900 }, { w: 390, h: 844 }]) {
    await capturePage({
      url: `${entry}?state=first-run`, name: `attachment-reading-progress-${viewport.w}.png`, viewport,
      action: async (page) => {
        await page.locator('#attach-button').click();
        await page.locator('#pdf-input').setInputFiles(samplePath);
        await page.getByRole('button', { name: 'Open sample pages' }).waitFor({ state: 'visible' });
        await page.getByRole('button', { name: 'Open sample pages' }).click();
        await page.waitForFunction(() => document.body.dataset.state === 'reading-progress' && !document.querySelector('#pdf-sheet').hidden && document.querySelector('#pdf-sheet').classList.contains('is-open'));
      },
    });
  }
  await capturePage({
    url: `${entry}?state=first-run`, name: 'first-run-to-answered-1440.png', viewport: { w: 1440, h: 900 },
    action: async (page) => {
      await page.locator('.suggestion').first().click();
      await page.locator('#send-button').click();
      await page.locator('.assistant-message').waitFor({ state: 'visible' });
      await page.waitForFunction(() => { const image=document.querySelector('.inline-figure img'); return image?.complete && image.naturalWidth > 0; });
    },
  });
  await capturePage({
    url: `${entry}?state=answered-closed`, name: 'answer-visible-1024.png', viewport: { w: 1024, h: 768 },
    action: async (page) => {
      await page.evaluate(() => { const app=document.querySelector('#app'); app.scrollTop=Math.max(0,app.scrollHeight-app.clientHeight-48); });
      await page.waitForTimeout(60);
    },
  });
  for (const sourceId of ['link-caption', 'stream-flow', 'john-klein-water']) {
    await capturePage({
      url: `${entry}?state=answered-closed`, name: `source-${sourceId}-390.png`, viewport: { w: 390, h: 844 },
      action: async (page) => {
        await page.locator(`.citation-chip[data-source-id="${sourceId}"]`).click();
        await page.waitForFunction((id) => document.querySelector('#evidence-layer').dataset.activeTarget === id && !document.querySelector('#pdf-sheet').hidden && document.querySelector('#pdf-sheet').classList.contains('is-open'), sourceId);
        await page.waitForTimeout(180);
      },
    });
  }
  console.log(`BROWSER ${browser.version()} Chrome channel`);
} finally {
  await browser.close();
}
