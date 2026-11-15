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
const waitForMobilePassage = async (page, id) => {
  await page.waitForFunction((targetId) => {
    const intersect = (a, b) => ({left:Math.max(a.left,b.left),top:Math.max(a.top,b.top),right:Math.min(a.right,b.right),bottom:Math.min(a.bottom,b.bottom)});
    const viewport = {left:0,top:0,right:innerWidth,bottom:innerHeight};
    const pageArea = document.querySelector('.page-area');
    const reader = document.querySelector('.reader');
    let clip = intersect(intersect(viewport,reader.getBoundingClientRect()),pageArea.getBoundingClientRect());
    for (let node=pageArea; node; node=node.parentElement) {
      const style=getComputedStyle(node), rect=node.getBoundingClientRect();
      if (['hidden','clip','auto','scroll'].includes(style.overflowX)) { clip.left=Math.max(clip.left,rect.left); clip.right=Math.min(clip.right,rect.right); }
      if (['hidden','clip','auto','scroll'].includes(style.overflowY)) { clip.top=Math.max(clip.top,rect.top); clip.bottom=Math.min(clip.bottom,rect.bottom); }
    }
    const target=document.querySelector(`.evidence-target[data-evidence-target="${targetId}"]`);
    const marks=[...(target?.querySelectorAll('i')||[])].map(mark=>mark.getBoundingClientRect());
    const line=document.querySelector('#leader-line');
    const bounds=marks.length ? {top:Math.min(...marks.map(mark=>mark.top)),bottom:Math.max(...marks.map(mark=>mark.bottom))} : null;
    const chip=document.querySelector(`.evidence-sentence[data-evidence-target="${targetId}"] .source-chip`)?.getBoundingClientRect();
    return document.querySelector('#evidence-highlight')?.dataset.activeTarget===targetId && line?.classList.contains('is-visible') && line.dataset.targetId===targetId && line.dataset.route==='mobile-gutter' && marks.length>0 &&
      marks.every(mark=>mark.left>=clip.left-1 && mark.right<=clip.right+1 && mark.top>=clip.top-1 && mark.bottom<=clip.bottom+1) &&
      bounds && clip.bottom-bounds.bottom>=20 && bounds.top-clip.top>=12 && chip && chip.top>=0 && chip.bottom<=innerHeight;
  }, id);
  await page.waitForTimeout(260);
};
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
        if (w === 390) await waitForMobilePassage(page, claimId);
        else {
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
      }
      const shot = path.join(root, 'shots', `${state}-${w}.png`);
      await page.screenshot({ path: shot, fullPage: false, animations: 'disabled' });
      console.log(`CAPTURED ${state} ${w}x${h} ${shot}`);
      await page.close();
    }
  }
  for (const claimId of ['link-caption', 'stream-flow']) {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
    await page.goto(`${base}?state=answered`, { waitUntil: 'networkidle' });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map((image) => image.decode().catch(() => undefined)));
    });
    await page.locator(`.evidence-sentence[data-evidence-target="${claimId}"]`).focus();
    await waitForMobilePassage(page, claimId);
    const shot = path.join(root, 'shots', `answered-${claimId}-390.png`);
    await page.screenshot({ path: shot, fullPage: false, animations: 'disabled' });
    console.log(`CAPTURED answered ${claimId} 390x844 ${shot}`);
    await page.close();
  }
} finally {
  await browser.close();
}
