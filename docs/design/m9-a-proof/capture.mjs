import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';

const require = createRequire(path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@playwright', 'cli', 'package.json'));
const { chromium } = require('playwright');
const root = path.resolve('docs/design/m9-a-proof');
const base = 'http://127.0.0.1:8765/proof-static-not-functional.html';
const viewports = [{ w: 1440, h: 900 }, { w: 1024, h: 768 }, { w: 390, h: 844 }];
const states = ['drop', 'reading', 'answered', 'model-missing'];
await fs.mkdir(path.join(root, 'shots'), { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  for (const state of states) {
    for (const { w, h } of viewports) {
      const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
      await page.goto(`${base}?state=${state}`, { waitUntil: 'networkidle' });
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all([...document.images].map((image) => image.decode().catch(() => undefined)));
      });
      if (state === 'answered') {
        const claimId = w === 1440 ? 'link-caption' : w === 1024 ? 'stream-flow' : 'john-klein-water';
        const sentence = page.locator(`.evidence-sentence[data-evidence-target="${claimId}"]`);
        await sentence.focus();
        await page.waitForFunction((id) => {
          const path = document.querySelector('#leader-line');
          const target = [...document.querySelectorAll('.evidence-target')].find((item) => item.dataset.evidenceTarget === id);
          const anchor = target?.querySelector('[data-leader-anchor]')?.getBoundingClientRect();
          const numbers = (path?.getAttribute('d') || '').match(/-?(?:\d+\.?\d*|\.\d+)/g)?.map(Number) || [];
          if (!path?.classList.contains('is-visible') || path.dataset.targetId !== id || !anchor) return false;
          const endpoint = path.dataset.route === 'mobile-gutter' ? [numbers[4], numbers[3]] : numbers.slice(-2);
          const expectedX = path.dataset.route === 'mobile-gutter' ? anchor.right : anchor.left;
          return endpoint.length === 2 && Math.hypot(endpoint[0] - expectedX, endpoint[1] - (anchor.top + anchor.height / 2)) < 2;
        }, claimId);
        await page.waitForTimeout(280);
      }
      const shot = path.join(root, 'shots', `${state}-${w}.png`);
      await page.screenshot({ path: shot, fullPage: false, animations: 'disabled' });
      console.log(`CAPTURED ${state} ${w}x${h} ${shot}`);
      await page.close();
    }
  }
} finally {
  await browser.close();
}
