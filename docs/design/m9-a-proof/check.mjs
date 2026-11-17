import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';

const require = createRequire(path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@playwright', 'cli', 'package.json'));
const { chromium } = require('playwright');
const root = path.resolve('docs/design/m9-a-proof');
const origin = 'http://127.0.0.1:8765';
const entry = `${origin}/proof-static-not-functional.html`;
const viewports = [
  { w: 1440, h: 900 },
  { w: 1024, h: 768 },
  { w: 768, h: 900 },
  { w: 390, h: 844 },
];
const obsoleteR4Shots = [
  'r4-answer-shapes-1440.png', 'r4-answered-closed-1440.png', 'r4-answered-open-1440.png',
  'r4-citation-popover-1440.png', 'r4-first-run-1440.png', 'r4-first-run-390.png',
  'r4-logo-comparison.png', 'r4-matches-1440.png', 'r4-mobile-answered-390.png',
  'r4-mobile-sheet-55-390.png', 'r4-model-unreachable-1440.png', 'r4-reading-progress-1440.png',
];
const output = [
  'M9-A-R5 — Final corrective static-proof checks',
  'Browser: Playwright-managed Chrome; exact version recorded after launch.',
  'Scope: static HTML/CSS and deterministic local interactions only. No backend retrieval, ingestion, generation, or production pdf.js behavior is verified.',
  'Evidence paths are relative to docs/design/m9-a-proof/. Screenshot-only readability and taste checks are identified separately.',
  '',
];
let passed = 0;
let failed = 0;
let notTested = 0;

function record(id, name, viewport, status, measured, evidence = 'CHECK-RESULTS.txt') {
  const result = typeof status === 'boolean' ? (status ? 'PASS' : 'FAIL') : status;
  output.push(`${id} | ${result} | ${name} | viewport=${viewport} | measured=${measured} | evidence=${evidence}`);
  if (result === 'PASS') passed += 1;
  else if (result === 'FAIL') failed += 1;
  else notTested += 1;
}

const wordCount = (value) => (value.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || []).length;
const waitAssets = (page) => page.evaluate(async () => {
  await document.fonts.ready;
  await Promise.all([...document.images].map((image) => image.decode().catch(() => undefined)));
});
const makePage = async (browser, state, viewport, options = {}) => {
  const context = await browser.newContext({
    viewport: { width: viewport.w, height: viewport.h },
    deviceScaleFactor: 1,
    reducedMotion: options.reducedMotion || 'no-preference',
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  const requests = [];
  const errors = [];
  const badResponses = [];
  page.on('request', (request) => requests.push(request.url()));
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => { if (response.status() >= 400) badResponses.push(`${response.status()} ${response.url()}`); });
  await page.addInitScript(() => {
    window.__r4Shifts = [];
    if (PerformanceObserver.supportedEntryTypes.includes('layout-shift')) {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) window.__r4Shifts.push({ value: entry.value, recentInput: entry.hadRecentInput });
      }).observe({ type: 'layout-shift', buffered: true });
    }
  });
  const url = `${entry}${state ? `?state=${encodeURIComponent(state)}` : ''}`;
  await page.goto(url, { waitUntil: 'networkidle' });
  await waitAssets(page);
  return { context, page, requests, errors, badResponses };
};

const pageMeasurements = (page, selector = '.assistant-answer') => page.evaluate((targetSelector) => {
  const target = document.querySelector(targetSelector);
  if (!target) return null;
  const counts = new Map();
  const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement?.closest('button,.citation,.figure-caption,.figure-credit')) continue;
    for (let i = 0; i < node.length; i += 1) {
      if (/\s/.test(node.data[i])) continue;
      const range = document.createRange();
      range.setStart(node, i);
      range.setEnd(node, i + 1);
      const rect = range.getBoundingClientRect();
      if (rect.width < 0.1 || rect.height < 0.1) continue;
      const key = Math.round(rect.top);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  const style = getComputedStyle(target);
  const fontSize = parseFloat(style.fontSize);
  const lineHeight = parseFloat(style.lineHeight);
  return {
    width: Math.round(target.getBoundingClientRect().width),
    fontSize,
    lineHeight,
    ratio: lineHeight / fontSize,
    lineLengths: [...counts.values()],
  };
}, selector);

const clipAndMarks = (page, sourceId) => page.evaluate((id) => {
  const intersect = (a, b) => ({ left: Math.max(a.left, b.left), top: Math.max(a.top, b.top), right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom) });
  const viewport = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
  const sheet = document.querySelector('#pdf-sheet');
  const view = document.querySelector('#page-viewport');
  const frame = document.querySelector('#page-frame');
  if (!sheet || sheet.hidden || !view || !frame) return null;
  let clip = intersect(viewport, sheet.getBoundingClientRect());
  clip = intersect(clip, view.getBoundingClientRect());
  for (let node = view.parentElement; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX)) { clip.left = Math.max(clip.left, rect.left); clip.right = Math.min(clip.right, rect.right); }
    if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY)) { clip.top = Math.max(clip.top, rect.top); clip.bottom = Math.min(clip.bottom, rect.bottom); }
  }
  const toolbar = sheet.querySelector('.sheet-tools');
  const toolbarRect = toolbar?.getBoundingClientRect();
  const toolbarVisible = !!toolbarRect && toolbarRect.width > 0 && toolbarRect.height > 0 && toolbarRect.top < clip.bottom && toolbarRect.bottom > clip.top;
  if (toolbarVisible) clip.bottom = Math.min(clip.bottom, toolbarRect.top);
  const target = document.querySelector(`.evidence-target[data-evidence-target="${id}"]`);
  const marks = [...(target?.querySelectorAll('.evidence-mark') || [])].map((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
  });
  const rect = frame.getBoundingClientRect();
  const union = marks.length ? {
    left: Math.min(...marks.map((mark) => mark.left)), top: Math.min(...marks.map((mark) => mark.top)),
    right: Math.max(...marks.map((mark) => mark.right)), bottom: Math.max(...marks.map((mark) => mark.bottom)),
  } : null;
  const zoom = window.__zoomInfo();
  return {
    clip, marks, union, toolbar: toolbarRect ? { left: toolbarRect.left, top: toolbarRect.top, right: toolbarRect.right, bottom: toolbarRect.bottom } : null,
    viewCenter: { x: (clip.left + clip.right) / 2, y: (clip.top + clip.bottom) / 2 },
    pageWidth: rect.width,
    pageHeight: rect.height,
    fitWidth: zoom.fitWidth,
    fitRelativeFactor: zoom.fitRelativeFactor,
    nativePdfPercent: zoom.nativePdfPercent,
    scroll: { left: view.scrollLeft, top: view.scrollTop, width: view.scrollWidth, height: view.scrollHeight, clientWidth: view.clientWidth, clientHeight: view.clientHeight },
  };
}, sourceId);

const ratio = (foreground, background) => {
  const luminance = (value) => {
    const parts = value.match(/[\d.]+/g)?.map(Number) || [];
    const channels = parts.slice(0, 3).map((component) => {
      const v = component / 255;
      return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const a = luminance(foreground), b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};

const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  output[1] = `Browser: Playwright-managed Chrome channel ${browser.version()} (headless).`;

  // C20–C22: shell geometry, corner radii and dividers.
  {
    const session = await makePage(browser, 'first-run', viewports[0]);
    const result = await session.page.evaluate(() => {
      const header = document.querySelector('.topbar');
      const style = getComputedStyle(header);
      return {
        background: style.backgroundColor,
        borderBottom: `${style.borderBottomStyle} ${style.borderBottomWidth} ${style.borderBottomColor}`,
        statusText: [...document.querySelectorAll('.attention-copy,.composer-feedback,#attention-feedback,.document-progress')].filter((node) => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden').map((node) => node.innerText.trim()).filter(Boolean),
        visibleStatusCount: [...document.querySelectorAll('.attention-copy,.composer-feedback,#attention-feedback,.document-progress')].filter((node) => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden' && node.innerText.trim()).length,
      };
    });
    const transparent = ['rgba(0, 0, 0, 0)', 'transparent'].includes(result.background);
    const noBorder = result.borderBottom.startsWith('none 0px') || result.borderBottom.includes('0px');
    record('C20', 'Ready header has no bar, border, or status text', '1440x900', transparent && noBorder && result.visibleStatusCount === 0, JSON.stringify(result), 'shots/r5-first-run-1440.png');

    const radius = await session.page.evaluate(() => {
      const allowed = ['.citation', '.citation-mark', '.page-thumb', '.document-thumb', '.document-thumb-frame', '.page-frame', '.page-image', '.pdf-sheet'];
      const findings = [];
      for (const element of document.querySelectorAll('body *')) {
        if (!element.getClientRects().length || allowed.some((selector) => element.matches(selector) || element.closest(selector))) continue;
        const style = getComputedStyle(element);
        const values = ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomRightRadius', 'borderBottomLeftRadius'].map((key) => parseFloat(style[key]));
        const small = values.filter((value) => value > 0 && value < 12);
        if (small.length) findings.push({ selector: element.id ? `#${element.id}` : element.className?.baseVal || element.className || element.tagName, radii: small });
      }
      return { smallNonExempt: findings, sheetRadius: getComputedStyle(document.querySelector('#pdf-sheet')).borderTopLeftRadius };
    });
    record('C21', 'No non-exempt visible radius is between 0 and 12px; PDF sheet is 16px', '1440x900', radius.smallNonExempt.length === 0 && parseFloat(radius.sheetRadius) === 16, JSON.stringify(radius), 'shots/r5-first-run-1440.png');

    await session.page.locator('.document-open').click();
    const dividers = await session.page.evaluate(() => {
      const separators = [...document.querySelectorAll('hr,[role="separator"],.divider')].filter((node) => node.getClientRects().length).map((node) => node.outerHTML.slice(0, 180));
      const fullWidthHairlines = [...document.querySelectorAll('body *')].filter((node) => {
        if (!node.getClientRects().length) return false;
        const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
        const visibleColor = (color) => color !== 'transparent' && !/rgba\([^)]*,\s*0\s*\)$/iu.test(color);
        const visibleBackground = visibleColor(style.backgroundColor);
        const visibleBorder = (parseFloat(style.borderTopWidth) > 0 && visibleColor(style.borderTopColor)) || (parseFloat(style.borderBottomWidth) > 0 && visibleColor(style.borderBottomColor));
        return rect.width >= innerWidth * 0.85 && rect.height <= 2 && (visibleBackground || visibleBorder);
      }).map((node) => node.id || node.className || node.tagName);
      const columnBorders = ['#app', '#pdf-sheet'].flatMap((selector) => {
        const style = getComputedStyle(document.querySelector(selector));
        return ['borderLeftWidth', 'borderRightWidth'].filter((key) => parseFloat(style[key]) > 0).map((key) => `${selector}:${key}=${style[key]}`);
      });
      return { separators, fullWidthHairlines, columnBorders, sheetBackground: getComputedStyle(document.querySelector('#pdf-sheet')).backgroundColor };
    });
    record('C22', 'No full-width hairline or chat/PDF divider', '1440x900', dividers.separators.length === 0 && dividers.fullWidthHairlines.length === 0 && dividers.columnBorders.length === 0, JSON.stringify(dividers), 'CHECK-RESULTS.txt (runtime DOM measurement)');
    await session.context.close();
  }

  // C23: measure full-width answer prose on real rendered gallery content.
  for (const viewport of viewports.slice(0, 2)) {
    const session = await makePage(browser, 'answer-shapes', viewport);
    const metrics = await pageMeasurements(session.page, '.fixture:nth-of-type(1) .assistant-answer');
    const renderedLines = metrics?.lineLengths || [];
    const fullLines = renderedLines.filter((value) => value >= 40);
    const average = fullLines.length ? fullLines.reduce((sum, value) => sum + value, 0) / fullLines.length : 0;
    const ok = !!metrics && average >= 55 && average <= 75 && metrics.ratio >= 1.55 && metrics.ratio <= 1.7;
    record('C23', 'Full-width paragraph measure and line-height', `${viewport.w}x${viewport.h}`, ok, JSON.stringify({ textColumnPx: metrics?.width, renderedLineCharacterCounts: renderedLines, fullLineAverage: Number(average.toFixed(1)), fontPx: metrics?.fontSize, lineHeightPx: metrics?.lineHeight, ratio: Number(metrics?.ratio.toFixed(3)), note: 'Figure-adjacent fixture D is reported separately; this measures full-width fixture A.' }), 'shots/r5-answer-shapes.png');
    await session.context.close();
  }

  // C24 and C32: all five real rendered fixtures at all four widths.
  for (const viewport of viewports) {
    const session = await makePage(browser, 'answer-shapes', viewport);
    const fixtureResult = await session.page.evaluate(() => {
      const fixtures = [...document.querySelectorAll('.fixture')];
      const documentWidth = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
      const app = document.querySelector('#app');
      return {
        count: fixtures.length,
        labels: fixtures.map((fixture) => fixture.querySelector('.fixture-label')?.innerText.trim()),
        documentWidth,
        appWidth: app.clientWidth,
        appScrollWidth: app.scrollWidth,
        answerWidths: fixtures.map((fixture) => Math.round(fixture.querySelector('.assistant-answer')?.getBoundingClientRect().width || 0)),
        figureCount: fixtures.map((fixture) => fixture.querySelectorAll('.inline-figure img').length),
        orderedListCount: fixtures.map((fixture) => fixture.querySelectorAll('ol').length),
        wordCountLong: (fixtures[2]?.querySelector('.assistant-answer')?.innerText.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || []).length,
        sentenceCountC: (fixtures[2]?.querySelector('.assistant-answer')?.innerText.match(/[.!?](?=\s|$)/gu) || []).length,
        insufficientLead: fixtures[4]?.querySelector('.no-answer')?.innerText.startsWith("I couldn't find this in the PDF") || false,
      };
    });
    const noOverflow = fixtureResult.documentWidth <= viewport.w + 1 && fixtureResult.appScrollWidth <= fixtureResult.appWidth + 1;
    const fixturesPass = fixtureResult.count === 5 && fixtureResult.figureCount[3] === 1 && fixtureResult.orderedListCount[1] === 1 && fixtureResult.sentenceCountC >= 3 && fixtureResult.sentenceCountC <= 4 && fixtureResult.insufficientLead;
    record('C32', 'Five grounded answer shapes render; Fixture C has four concise sentences', `${viewport.w}x${viewport.h}`, fixturesPass && noOverflow, JSON.stringify({ ...fixtureResult, noHorizontalOverflow: noOverflow, fixtureCRequirement: '3–4 grounded sentences; no minimum word count' }), 'shots/r5-answer-shapes.png');

    const chipResult = await session.page.evaluate(() => {
      const rows = [];
      for (const pair of document.querySelectorAll('.citation-pair')) {
        const button = pair.querySelector('.citation');
        const walker = document.createTreeWalker(pair, NodeFilter.SHOW_TEXT);
        let last = null;
        while (walker.nextNode()) if (!walker.currentNode.parentElement?.closest('button')) last = walker.currentNode;
        if (!button || !last) { rows.push({ missing: true }); continue; }
        const match = /(\S+)\s*$/.exec(last.data);
        if (!match) { rows.push({ missing: true }); continue; }
        const range = document.createRange();
        range.setStart(last, match.index);
        range.setEnd(last, match.index + match[1].length);
        const word = range.getBoundingClientRect();
        const chip = button.querySelector('.citation-mark').getBoundingClientRect();
        const lineDelta = Math.abs((word.top + word.bottom) / 2 - (chip.top + chip.bottom) / 2);
        rows.push({ source: button.dataset.sourceId, lastWord: match[1], lineDelta: Number(lineDelta.toFixed(1)), pairWhitespace: getComputedStyle(pair).whiteSpace, sameVisualLine: lineDelta < 24 });
      }
      return rows;
    });
    const wrapping = chipResult.filter((value) => !value.sameVisualLine || value.pairWhitespace !== 'nowrap' || value.missing);
    record('C24', 'Citation chip remains with the last word at every answer shape', `${viewport.w}x${viewport.h}`, chipResult.length >= 3 && wrapping.length === 0, JSON.stringify({ citationsMeasured: chipResult.length, violations: wrapping, samples: chipResult.slice(0, 6) }), 'shots/r5-answer-shapes.png');

    // Scroll each answer to its final prose character and verify it is visible, not clipped by a fixed composer.
    const access = [];
    for (let index = 0; index < 5; index += 1) {
      const answer = session.page.locator('.fixture').nth(index).locator('.assistant-answer');
      await answer.scrollIntoViewIfNeeded();
      const measured = await answer.evaluate((node) => {
        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
        let last = null;
        while (walker.nextNode()) {
          if (walker.currentNode.parentElement?.closest('button,.figure-caption,.figure-credit')) continue;
          if (walker.currentNode.data.trim()) last = walker.currentNode;
        }
        if (!last) return { found: false };
        const end = last.data.search(/\s*$/);
        const start = Math.max(0, end - 1);
        const range = document.createRange();
        range.setStart(last, start); range.setEnd(last, end);
        const rect = range.getBoundingClientRect();
        const app = document.querySelector('#app').getBoundingClientRect();
        return { found: true, lastGlyph: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }, app: { top: app.top, bottom: app.bottom, left: app.left, right: app.right }, scrollHeight: document.body.scrollHeight, bodyHeight: document.body.clientHeight };
      });
      access.push({ fixture: String.fromCharCode(65 + index), ...measured });
    }
    const inaccessible = access.filter((value) => !value.found || value.lastGlyph.bottom > value.app.bottom + 1 || value.lastGlyph.top < value.app.top - 1 || value.lastGlyph.left < 0 || value.lastGlyph.right > viewport.w + 1);
    record('C32', 'All five fixture endings are reachable by scrolling the rendered gallery', `${viewport.w}x${viewport.h}`, inaccessible.length === 0, JSON.stringify({ scrollableDocument: access[2]?.scrollHeight > access[2]?.bodyHeight, inaccessible, endingRects: access.map(({ fixture, lastGlyph }) => ({ fixture, lastGlyph })) }), 'shots/r5-answer-shapes.png');

    if (viewport.w >= 1024) {
      const figure = await session.page.evaluate(() => {
        const lead = document.querySelector('.fixture:nth-of-type(4) .answer-lead')?.getBoundingClientRect();
        const figureRect = document.querySelector('.fixture:nth-of-type(4) .inline-figure')?.getBoundingClientRect();
        return { lead: lead && { left: lead.left, right: lead.right, top: lead.top, bottom: lead.bottom }, figure: figureRect && { left: figureRect.left, right: figureRect.right, top: figureRect.top, bottom: figureRect.bottom }, intersects: !!lead && !!figureRect && lead.left < figureRect.right && lead.right > figureRect.left && lead.top < figureRect.bottom && lead.bottom > figureRect.top };
      });
      record('R3-1', 'Genuine figure sits beside its supporting first paragraph on desktop', `${viewport.w}x${viewport.h}`, !!figure.lead && !!figure.figure && !figure.intersects, JSON.stringify(figure), 'shots/r5-answer-shapes.png');
    } else {
      const figure = await session.page.evaluate(() => {
        const lead = document.querySelector('.fixture:nth-of-type(4) .answer-lead')?.getBoundingClientRect();
        const figureRect = document.querySelector('.fixture:nth-of-type(4) .inline-figure')?.getBoundingClientRect();
        return { lead: lead && { top: lead.top, bottom: lead.bottom }, figure: figureRect && { top: figureRect.top, bottom: figureRect.bottom }, followsLead: !!lead && !!figureRect && figureRect.top >= lead.bottom };
      });
      record('R3-1', 'Figure follows its supporting paragraph at narrow width', `${viewport.w}x${viewport.h}`, !!figure.lead && !!figure.figure && figure.followsLead, JSON.stringify(figure), 'CHECK-RESULTS.txt (runtime DOM order measurement)');
    }
    await session.context.close();
  }

  // Retained R3 reading visibility: the final answer glyph can be brought above the fixed composer.
  for (const viewport of viewports) {
    const session = await makePage(browser, 'answered-closed', viewport);
    const visibility = await session.page.evaluate(async () => {
      const app = document.querySelector('#app');
      app.scrollTop = app.scrollHeight;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const answer = document.querySelector('.assistant-answer');
      const walker = document.createTreeWalker(answer, NodeFilter.SHOW_TEXT);
      const glyphs = [];
      while (walker.nextNode()) {
        const text = walker.currentNode;
        if (!text.data.trim()) continue;
        for (let i = text.length - 1; i >= 0; i -= 1) {
          if (/\s/.test(text.data[i])) continue;
          const range = document.createRange(); range.setStart(text, i); range.setEnd(text, i + 1);
          const rect = range.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) { glyphs.push(rect); break; }
        }
      }
      const last = glyphs.at(-1), composer = document.querySelector('#composer').getBoundingClientRect();
      const finalGlyph = last && { left: last.left, top: last.top, right: last.right, bottom: last.bottom };
      return { finalGlyph, composer: { left: composer.left, top: composer.top, right: composer.right, bottom: composer.bottom }, aboveComposer: !!last && last.bottom <= composer.top, withinViewport: !!last && last.top >= 0 && last.bottom <= innerHeight, documentWidth: document.documentElement.scrollWidth, horizontalOverflow: document.documentElement.scrollWidth > innerWidth };
    });
    record('R3-5', 'Final answer glyph is reachable above the composer', `${viewport.w}x${viewport.h}`, visibility.aboveComposer && visibility.withinViewport && !visibility.horizontalOverflow, JSON.stringify(visibility), viewport.w === 1024 ? 'CHECK-RESULTS.txt (scrolled browser state; no screenshot slot)' : 'CHECK-RESULTS.txt (scrolled browser state)');
    await session.context.close();
  }

  // R3 layout-shift evidence: initial load, deterministic answer arrival, and PDF opening are separate.
  {
    const session = await makePage(browser, 'first-run', viewports[0]);
    const readShifts = () => session.page.evaluate(() => window.__r4Shifts || null);
    const summarizeShifts = (entries) => entries === null ? null : ({ raw: entries.map((entry) => Number(entry.value.toFixed(6))), cls: Number(entries.filter((entry) => !entry.recentInput).reduce((sum, entry) => sum + entry.value, 0).toFixed(6)), totalRaw: Number(entries.reduce((sum, entry) => sum + entry.value, 0).toFixed(6)) });
    await session.page.waitForTimeout(100);
    const initial = summarizeShifts(await readShifts());
    await session.page.evaluate(() => { window.__r4Shifts.length = 0; });
    await session.page.locator('.suggestion').first().click();
    await session.page.locator('#send-button').click();
    await session.page.waitForFunction(() => !!document.querySelector('.assistant-answer'));
    await waitAssets(session.page);
    await session.page.waitForTimeout(80);
    const answerArrival = summarizeShifts(await readShifts());
    await session.page.evaluate(() => { window.__r4Shifts.length = 0; document.querySelector('.citation').click(); });
    await session.page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    await session.page.waitForTimeout(280);
    const pdfOpening = summarizeShifts(await readShifts());
    record('R3-5', 'Layout shift measured separately for load, answer arrival, and PDF opening', '1440x900', initial !== null && answerArrival !== null && pdfOpening !== null && initial.cls === 0 && answerArrival.cls === 0, JSON.stringify({ initialLoad: initial, answerArrival: answerArrival, pdfOpening: pdfOpening, note: 'CLS excludes entries marked hadRecentInput; raw entries and total raw shift are retained for each phase.' }), 'CHECK-RESULTS.txt');
    await session.context.close();
  }

  // C25–C28 and C33: computed colours, fonts, copy and first-run composition.
  {
    const session = await makePage(browser, 'first-run', viewports[0]);
    const colors = await session.page.evaluate(() => {
      const lime = /rgba?\(\s*215\s*,\s*255\s*,\s*58/i;
      const allowed = (node) => node.matches('.brand-mark,.document-thumb-highlight,.hero-evidence-mark,.citation-mark,.evidence-mark,.citation-popover .popover-quote,.match-tag[aria-pressed="true"],.match-mobile button[aria-pressed="true"],.legend-mark') || !!node.closest('.brand-mark,.document-thumb-highlight,.hero-evidence-mark,.citation-mark,.evidence-mark,.citation-popover .popover-quote,.match-tag[aria-pressed="true"],.match-mobile button[aria-pressed="true"],.legend-mark');
      const uses = [];
      for (const node of document.querySelectorAll('body *')) {
        if (!node.getClientRects().length) continue;
        const style = getComputedStyle(node);
        const values = ['color', 'backgroundColor', 'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor', 'outlineColor', 'boxShadow'].map((key) => [key, style[key]]);
        for (const [property, value] of values) if (lime.test(value)) uses.push({ node: node.id || node.className?.baseVal || node.className || node.tagName, property, value, allowed: allowed(node) });
      }
      return { uses, unexpected: uses.filter((item) => !item.allowed) };
    });
    record('C25', 'Lime appears only on document evidence and the provisional logo', '1440x900', colors.unexpected.length === 0, JSON.stringify(colors), 'shots/r5-first-run-1440.png');

    const mono = await session.page.evaluate(() => [...document.querySelectorAll('body *')].filter((node) => node.getClientRects().length && /IBM Plex Mono/i.test(getComputedStyle(node).fontFamily) && !node.closest('[data-role="page-ref"]')).map((node) => ({ id: node.id, className: node.className?.baseVal || node.className, family: getComputedStyle(node).fontFamily })));
    record('C26', 'Monospace is limited to page-reference data', '1440x900', mono.length === 0, JSON.stringify({ unexpected: mono }), 'shots/r5-first-run-1440.png');

    const firstRun = await session.page.evaluate(() => {
      const rect = document.querySelector('#composer').getBoundingClientRect();
      return { centerY: Number((rect.top + rect.height / 2).toFixed(2)), viewportCenterY: innerHeight / 2, deviation: Number(Math.abs((rect.top + rect.height / 2) - innerHeight / 2).toFixed(2)), deviationPct: Number((Math.abs((rect.top + rect.height / 2) - innerHeight / 2) / innerHeight * 100).toFixed(2)), sample: document.querySelector('.document-name')?.textContent.trim(), metadata: document.querySelector('.document-meta')?.textContent.trim(), suggestions: [...document.querySelectorAll('.suggestion')].map((node) => node.textContent.trim()), pdfOpen: !document.querySelector('#pdf-sheet').hidden };
    });
    record('C28', 'R4 centered-composer placement is superseded by the R5 document-led first-run composition', '1440x900', 'NOT_TESTED', JSON.stringify({ priorMeasurement: firstRun, reason: 'R5 explicitly replaces centered chat with a left composer and right document preview.' }), 'R5-CHANGES.md');
    const chromeWords = await session.page.evaluate(() => {
      const excluded = '.suggestion,.user-turn,.assistant-answer,.figure-caption,.figure-credit,.popover-quote,.sr-only,.skip-link';
      const parts = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!node.data.trim() || node.parentElement?.closest(`script,style,[hidden],${excluded}`)) continue;
        if (!node.parentElement?.getClientRects().length || getComputedStyle(node.parentElement).visibility === 'hidden') continue;
        parts.push(node.data.trim());
      }
      const placeholder = document.querySelector('#question-input').getAttribute('placeholder') || '';
      const suggestions = [...document.querySelectorAll('.suggestion')].map((node) => node.innerText.trim());
      const count = (text) => (text.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []).filter((word) => /\p{L}/u.test(word)).length;
      return { chromeText: parts.join(' '), chromeWords: count(parts.join(' ')) + count(placeholder), placeholder, suggestions, suggestionWords: suggestions.reduce((sum, text) => sum + count(text), 0), totalIncludingSuggestions: count(parts.join(' ')) + count(placeholder) + suggestions.reduce((sum, text) => sum + count(text), 0) };
    });
    record('C33', 'First-run copy audit separates chrome from required sample questions', '1440x900', chromeWords.chromeWords <= 25, JSON.stringify(chromeWords), 'copy.md; shots/r5-first-run-1440.png');
    await session.context.close();

    const mobile = await makePage(browser, 'first-run', viewports[3]);
    const mobileMetrics = await mobile.page.evaluate(() => {
      const rect = document.querySelector('#composer').getBoundingClientRect();
      return { centerY: rect.top + rect.height / 2, viewportCenterY: innerHeight / 2, deviationPct: Math.abs(rect.top + rect.height / 2 - innerHeight / 2) / innerHeight * 100, scrollWidth: document.documentElement.scrollWidth };
    });
    record('C28', 'R4 mobile composer-centering assertion is superseded by R5 document-first mobile order', '390x844', 'NOT_TESTED', JSON.stringify({ priorMeasurement: mobileMetrics, reason: 'R5 positions a real page preview above the composer and suggestions below the preview.' }), 'R5-CHANGES.md');
    await mobile.context.close();
  }

  // C27: computed visible text sizes and composited foreground/background contrast.
  for (const [state, viewport] of [['first-run', viewports[0]], ['answered-open', viewports[0]], ['answered-open', viewports[1]], ['answered-open', viewports[2]], ['answered-open', viewports[3]], ['model-unreachable', viewports[0]]]) {
    const session = await makePage(browser, state, viewport);
    if (state === 'answered-open') await session.page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    const values = await session.page.evaluate(() => {
      const parse = (value) => {
        if (/^#[\da-f]{6}$/iu.test(value)) return { r: parseInt(value.slice(1, 3), 16), g: parseInt(value.slice(3, 5), 16), b: parseInt(value.slice(5, 7), 16), a: 1 };
        const nums = value.match(/[\d.]+/g)?.map(Number) || [];
        return nums.length >= 3 ? { r: nums[0], g: nums[1], b: nums[2], a: nums.length > 3 ? nums[3] : 1 } : null;
      };
      const over = (front, back) => ({ r: front.r * front.a + back.r * (1 - front.a), g: front.g * front.a + back.g * (1 - front.a), b: front.b * front.a + back.b * (1 - front.a), a: 1 });
      const luminance = (color) => {
        const fn = (channel) => { const v = channel / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return .2126 * fn(color.r) + .7152 * fn(color.g) + .0722 * fn(color.b);
      };
      const ratio = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
      const root = parse('#16140f');
      const bad = [];
      let minimumSize = Infinity, minimumContrast = Infinity;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const text = walker.currentNode;
        const element = text.parentElement;
        if (!text.data.trim() || !element || !element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') continue;
        let sizeNode = element;
        while (sizeNode && !sizeNode.getClientRects().length) sizeNode = sizeNode.parentElement;
        const font = parseFloat(getComputedStyle(sizeNode).fontSize);
        minimumSize = Math.min(minimumSize, font);
        let background = root;
        const chain = [];
        for (let node = element; node; node = node.parentElement) chain.unshift(node);
        for (const node of chain) {
          const color = parse(getComputedStyle(node).backgroundColor);
          if (color && color.a > 0) background = over(color, background);
        }
        const foreground = parse(getComputedStyle(element).color);
        const value = foreground ? ratio(foreground, background) : 0;
        minimumContrast = Math.min(minimumContrast, value);
        if (font < 12 || value < 4.5) bad.push({ text: text.data.trim().slice(0, 42), fontPx: font, contrast: Number(value.toFixed(2)), foreground: getComputedStyle(element).color, background: `rgb(${Math.round(background.r)}, ${Math.round(background.g)}, ${Math.round(background.b)})` });
      }
      return { minimumTextPx: minimumSize, minimumContrast: Number(minimumContrast.toFixed(2)), violations: bad.slice(0, 12), testedTextNodes: document.querySelectorAll('body *').length };
    });
    record('C27', 'Visible DOM text is at least 12px and contrast is at least 4.5:1', `${viewport.w}x${viewport.h} ${state}`, values.violations.length === 0, JSON.stringify(values), state === 'first-run' ? 'shots/r5-first-run-1440.png' : state === 'model-unreachable' ? 'shots/r5-model-unreachable-1440.png' : 'CHECK-RESULTS.txt (runtime DOM measurement)');
    await session.context.close();
  }

  // C29: citation opens the actual static page and positions its verified marks within the reading clip.
  for (const viewport of viewports) {
    const session = await makePage(browser, 'answered-closed', viewport);
    await session.page.locator('.citation').first().click();
    await session.page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#evidence-layer').dataset.activeTarget === 'link-caption');
    await session.page.waitForTimeout(180);
    const value = await clipAndMarks(session.page, 'link-caption');
    const within = !!value && value.marks.length > 0 && value.marks.every((mark) => mark.left >= value.clip.left - 1 && mark.right <= value.clip.right + 1 && mark.top >= value.clip.top - 1 && mark.bottom <= value.clip.bottom + 1);
    const dx = value ? Math.abs((value.union.left + value.union.right) / 2 - value.viewCenter.x) / Math.max(1, value.clip.right - value.clip.left) : 1;
    const dy = value ? Math.abs((value.union.top + value.union.bottom) / 2 - value.viewCenter.y) / Math.max(1, value.clip.bottom - value.clip.top) : 1;
    const zoomPass = !!value && value.pageWidth >= value.fitWidth * .98 && (value.nativePdfPercent === null || value.nativePdfPercent <= 200);
    record('C29', 'Citation 1 opens and centers the full Link caption highlight within the clipped PDF view', `${viewport.w}x${viewport.h}`, within && dx <= .15 && dy <= .15 && zoomPass, JSON.stringify({ target: 'link-caption', pageLabel: await session.page.locator('#toolbar-page').innerText(), zoom: value && { nativePdfPercent: value.nativePdfPercent, fitRelativeFactor: value.fitRelativeFactor }, pageWidth: value?.pageWidth, fitWidth: value?.fitWidth, clip: value?.clip, targetRect: value?.union, centerDeviation: { xPct: Number((dx * 100).toFixed(2)), yPct: Number((dy * 100).toFixed(2)) }, minimumClearancePx: value ? Math.min(value.union.left - value.clip.left, value.clip.right - value.union.right, value.union.top - value.clip.top, value.clip.bottom - value.union.bottom) : null, marks: value?.marks, scroll: value?.scroll }), viewport.w === 390 ? 'shots/r5-mobile-sheet-92.png (selected passage at 92% snap); runtime geometry for this viewport' : 'CHECK-RESULTS.txt (runtime DOM measurement; no matching saved R5 open-sheet screenshot)');
    await session.context.close();
  }

  // All three verified cited passages must remain completely visible in the 390px PDF sheet.
  {
    const session = await makePage(browser, 'answered-closed', viewports[3]);
    const sources = [];
    for (let index = 0; index < 3; index += 1) {
      const citation = session.page.locator('.citation').nth(index);
      const sourceId = await citation.getAttribute('data-source-id');
      await citation.click();
      await session.page.waitForFunction((id) => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#evidence-layer').dataset.activeTarget === id, sourceId);
      await session.page.waitForTimeout(120);
      const value = await clipAndMarks(session.page, sourceId);
      const within = !!value && value.marks.length > 0 && value.marks.every((mark) => mark.left >= value.clip.left - 1 && mark.right <= value.clip.right + 1 && mark.top >= value.clip.top - 1 && mark.bottom <= value.clip.bottom + 1);
      const dx = value ? Math.abs((value.union.left + value.union.right) / 2 - value.viewCenter.x) / Math.max(1, value.clip.right - value.clip.left) : 1;
      const dy = value ? Math.abs((value.union.top + value.union.bottom) / 2 - value.viewCenter.y) / Math.max(1, value.clip.bottom - value.clip.top) : 1;
      const minimumLineHeight = value?.marks.length ? Math.min(...value.marks.map((mark) => mark.height)) : 0;
      sources.push({ sourceId, page: await session.page.locator('#toolbar-page').innerText(), zoom: value?.nativePdfPercent, pageWidth: value?.pageWidth, clip: value?.clip, marks: value?.marks, minimumLineHeight, markUnion: value?.union, centerDeviationPct: { x: Number((dx * 100).toFixed(2)), y: Number((dy * 100).toFixed(2)) }, clearancePx: value ? Math.min(value.union.left - value.clip.left, value.clip.right - value.union.right, value.union.top - value.clip.top, value.clip.bottom - value.union.bottom) : null, within, centered: dx <= .15 && dy <= .15, readableScale: minimumLineHeight >= 16 });
      if (index < 2) {
        await session.page.locator('#close-sheet').click();
        await session.page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
      }
    }
    const allVisible = sources.length === 3 && sources.every((item) => item.within && item.centered && item.zoom <= 200 && item.page === '2 / 4' && item.readableScale);
    record('R4-mobile-sources', 'R4 fixed 118% mobile zoom and 1.09px clearance gate is superseded by R5 nine-snap geometry', '390x844', 'NOT_TESTED', JSON.stringify({ priorMeasurement: sources, reason: 'R5 requires all three passages at nine sheet-height combinations with at least 8px effective clearance.' }), 'R5-CHANGES.md; CHECK-RESULTS.txt');
    await session.context.close();
  }

  // C30: the method rail and its short leader stay in the dark gutter outside the page image.
  {
    const session = await makePage(browser, 'answered-open', viewports[0]);
    await session.page.locator('#matches-toggle').click();
    await session.page.waitForFunction(() => {
      const page = document.querySelector('#page-frame').getBoundingClientRect();
      const viewport = document.querySelector('#page-viewport').getBoundingClientRect();
      const tags = [...document.querySelectorAll('.match-tag')];
      return tags.length > 0 && tags.every((node) => { const rect = node.getBoundingClientRect(); return rect.left >= page.right && rect.right <= viewport.right && rect.top >= viewport.top && rect.bottom <= viewport.bottom; });
    });
    const rail = await session.page.evaluate(() => {
      const page = document.querySelector('#page-frame').getBoundingClientRect();
      const viewportNode = document.querySelector('#page-viewport');
      const viewportRect = viewportNode.getBoundingClientRect();
      const viewport = { left: viewportRect.left + viewportNode.clientLeft, right: viewportRect.left + viewportNode.clientLeft + viewportNode.clientWidth, top: viewportRect.top + viewportNode.clientTop, bottom: viewportRect.top + viewportNode.clientTop + viewportNode.clientHeight };
      const tags = [...document.querySelectorAll('.match-tag')].map((node) => {
        const r = node.getBoundingClientRect();
        const before = getComputedStyle(node, '::before');
        return { text: node.innerText.trim(), rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom }, leaderWidth: before.width, leaderLeft: before.left, overlapsRasterPage: r.left < page.right && r.right > page.left, insideVisibleGutter: r.left >= page.right && r.right <= viewport.right && r.top >= viewport.top && r.bottom <= viewport.bottom };
      });
      return { page: { left: page.left, right: page.right }, viewport, tags, legendVisible: document.querySelector('#match-legend').getClientRects().length > 0, rasterOnly: !document.querySelector('#page-frame .textLayer,.textLayer') };
    });
    const noCollision = rail.tags.length > 0 && rail.tags.every((tag) => !tag.overlapsRasterPage && tag.insideVisibleGutter);
    record('C30', 'Method labels stay visible in the dark gutter beside the page', '1440x900', noCollision && rail.legendVisible, JSON.stringify({ ...rail, limitation: 'The proof uses a rasterized Poppler page and has no pdf.js text layer. Gutter geometry is verified against the full page rectangle; production text-layer collision is untested.' }), 'CHECK-RESULTS.txt (runtime DOM measurement)');
    await session.context.close();
  }

  // C31: actual mobile snap dimensions, composer visibility and touch targets.
  {
    const session = await makePage(browser, 'answered-closed', viewports[3]);
    await session.page.locator('.citation').first().click();
    const grip = session.page.locator('#sheet-grip');
    await grip.focus();
    const snaps = [];
    for (const action of ['End', 'ArrowUp', 'Home']) {
      await grip.press(action);
      const value = await session.page.evaluate(() => {
        const sheet = document.querySelector('#pdf-sheet').getBoundingClientRect();
        const composer = document.querySelector('#composer');
        const composerStyle = getComputedStyle(composer);
        const targets = [...document.querySelectorAll('#pdf-sheet button')].filter((node) => node.getClientRects().length).map((node) => {
          const rect = node.getBoundingClientRect();
          return { label: node.getAttribute('aria-label') || node.innerText.trim(), width: Math.round(rect.width), height: Math.round(rect.height) };
        });
        return { snap: document.body.dataset.sheetSnap, sheetHeight: Number(sheet.height.toFixed(1)), heightPct: Number((sheet.height / innerHeight * 100).toFixed(1)), composerVisibility: composerStyle.visibility, minTargetPx: Math.min(...targets.flatMap((item) => [item.width, item.height])), undersized: targets.filter((item) => item.width < 44 || item.height < 44) };
      });
      snaps.push(value);
    }
    const expected = ['30', '55', '92'];
    const correct = snaps[0].snap === expected[0] && snaps[1].snap === expected[1] && snaps[2].snap === expected[2]
      && Math.abs(snaps[0].heightPct - 30) <= 1 && Math.abs(snaps[1].heightPct - 55) <= 1 && Math.abs(snaps[2].heightPct - 92) <= 1
      && snaps[0].composerVisibility !== 'hidden' && snaps[1].composerVisibility === 'hidden' && snaps[2].composerVisibility === 'hidden'
      && snaps.every((item) => item.undersized.length === 0);
    for (const item of snaps) record('C31', `Mobile PDF snap ${item.snap}%`, '390x844', correct ? 'PASS' : 'FAIL', JSON.stringify(item), `shots/r5-mobile-sheet-${item.snap}.png`);
    await session.context.close();
  }

  // C33: banned expressions across all directly addressable states and chrome word counts.
  {
    const banned = ['desk', 'belong', 'page-backed', 'read then ask', 'seamless', 'effortless', 'unlock', 'supercharge', 'magic', 'intelligent', 'powered by ai', 'powered by', 'your documents', 'journey', 'insights', 'smart', 'model ready', 'preview', 'illustrative', 'not functional'];
    const states = ['first-run', 'answered-closed', 'answered-open', 'answered-matches', 'model-unreachable', 'reading-progress', 'answer-shapes'];
    const findings = [];
    for (const state of states) {
      const session = await makePage(browser, state, viewports[0]);
      if (state === 'answered-open' || state === 'answered-matches' || state === 'reading-progress') await session.page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
      const visible = await session.page.evaluate(() => {
        const copy = [...document.querySelectorAll('body *')].filter((node) => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
        return copy.flatMap((node) => [node.innerText, node.getAttribute('aria-label'), node.getAttribute('title'), node.getAttribute('placeholder')].filter(Boolean)).join(' ');
      });
      const normalized = visible.toLowerCase().replace(/[\p{P}\p{S}]+/gu, ' ').replace(/\s+/g, ' ').trim();
      const matches = banned.filter((phrase) => normalized.includes(phrase.toLowerCase().replace(/[\p{P}\p{S}]+/gu, ' ').replace(/\s+/g, ' ').trim()));
      const copyAudit = await session.page.evaluate(() => {
        const selectors = ['.topbar', '#attention', '#composer', '#pdf-sheet', '#menu-panel', '#settings-panel', '#help-panel', '.citation-popover'];
        const roots = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]).filter((node) => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
        const excluded = '.sr-only,.suggestion,.user-turn,.assistant-answer,.figure-caption,.figure-credit,.popover-quote';
        const parts = [];
        const visibleWord = (range) => {
          for (const textRect of range.getClientRects()) {
            let clip = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
            let visible = true;
            for (let element = range.startContainer.parentElement; element && element !== document.documentElement; element = element.parentElement) {
              const style = getComputedStyle(element);
              if (element.hidden || style.display === 'none' || style.visibility === 'hidden' || style.contentVisibility === 'hidden' || Number(style.opacity) === 0 || element.matches('.sr-only')) { visible = false; break; }
              const overflowX = style.overflowX;
              const overflowY = style.overflowY;
              if (overflowX !== 'visible' || overflowY !== 'visible') {
                const box = element.getBoundingClientRect();
                const left = box.left + element.clientLeft;
                const top = box.top + element.clientTop;
                if (overflowX !== 'visible') { clip.left = Math.max(clip.left, left); clip.right = Math.min(clip.right, left + element.clientWidth); }
                if (overflowY !== 'visible') { clip.top = Math.max(clip.top, top); clip.bottom = Math.min(clip.bottom, top + element.clientHeight); }
              }
            }
            if (visible && textRect.right > clip.left && textRect.left < clip.right && textRect.bottom > clip.top && textRect.top < clip.bottom) return true;
          }
          return false;
        };
        for (const root of roots) {
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {
            const node = walker.currentNode;
            if (!node.data.trim() || node.parentElement?.closest(`[hidden],${excluded}`) || !node.parentElement?.getClientRects().length) continue;
            const matcher = /[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu;
            for (const match of node.data.matchAll(matcher)) {
              if (!/\p{L}/u.test(match[0])) continue; // Page numbers and citation numbers are references, not copy words.
              const range = document.createRange();
              range.setStart(node, match.index);
              range.setEnd(node, match.index + match[0].length);
              if (visibleWord(range)) parts.push(match[0]);
            }
          }
        }
        const placeholder = document.querySelector('#question-input')?.getAttribute('placeholder') || '';
        const suggestions = [...document.querySelectorAll('.suggestion')].filter((node) => node.getClientRects().length).map((node) => node.innerText.trim());
        const count = (text) => (text.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []).filter((word) => /\p{L}/u.test(word)).length;
        const chromeWords = count(parts.join(' ')) + count(placeholder);
        const suggestionWords = suggestions.reduce((sum, text) => sum + count(text), 0);
        return { chromeText: parts.join(' '), chromeWords, countNote: 'Visible alphabetic words only; numeric page labels and citation indices are document references, not interface copy.', placeholder, suggestions, suggestionWords, totalIncludingSuggestions: chromeWords + suggestionWords };
      });
      const ollamaLocations = await session.page.evaluate(() => [...document.querySelectorAll('.attention-copy,#settings-panel')].filter((node) => node.getClientRects().length && /ollama/i.test([node.innerText, node.getAttribute('aria-label'), node.getAttribute('title')].filter(Boolean).join(' '))).map((node) => ({ selector: node.matches('.attention-copy') ? '.attention-copy' : '#settings-panel', text: node.innerText.trim().slice(0, 100) })));
      const ollamaAllowed = state === 'model-unreachable' ? ollamaLocations.length === 1 && ollamaLocations[0].selector === '.attention-copy' : ollamaLocations.length === 0;
      const externalRequests = session.requests.filter((url) => new URL(url).origin !== origin);
      record('C33', `Visible interface copy word count: ${state}`, '1440x900', copyAudit.chromeWords <= 25, JSON.stringify(copyAudit), `?state=${state}; copy.md`);
      record('C33', `Banned visible wording and Ollama placement: ${state}`, '1440x900', matches.length === 0 && ollamaAllowed && externalRequests.length === 0, JSON.stringify({ bannedMatches: matches, ollamaLocations, externalRequests, normalizedVisibleCopy: normalized.slice(0, 700) }), `?state=${state}`);
      findings.push({ state, banned: matches, ollamaAllowed, externalRequests: externalRequests.length, chromeWords: copyAudit.chromeWords, chromeText: copyAudit.chromeText, placeholder: copyAudit.placeholder, totalIncludingSuggestions: copyAudit.totalIncludingSuggestions });
      await session.context.close();
    }
    const startingState = await makePage(browser, 'starting', viewports[0]);
    const startingVisible = await startingState.page.locator('body').innerText();
    const startingMetrics = { actualState: await startingState.page.locator('body').getAttribute('data-state'), visibleStartingCopy: startingVisible.includes('Getting the model ready'), readyCopy: startingVisible.includes('Model ready'), attentionVisible: await startingState.page.locator('#attention').isVisible() };
    record('C33', 'Removed starting URL falls back to first run with no status or ready claim', '1440x900', startingMetrics.actualState === 'first-run' && !startingMetrics.visibleStartingCopy && !startingMetrics.readyCopy && !startingMetrics.attentionVisible, JSON.stringify(startingMetrics), 'copy.md; proof-static-not-functional.html');
    await startingState.context.close();

    const standaloneCuriosity = await makePage(browser, 'first-run', viewports[0]);
    const curiosityLabels = await standaloneCuriosity.page.evaluate(() => [...document.querySelectorAll('body *')].filter((node) => node.getClientRects().length && node.children.length === 0 && node.textContent.trim().toLowerCase() === 'curiosity' && !node.matches('.document-name,.sheet-title')).map((node) => ({ className: node.className?.baseVal || node.className, text: node.textContent.trim() })));
    record('C33', 'Curiosity is not used as a standalone interface label', '1440x900', curiosityLabels.length === 0, JSON.stringify({ standaloneCuriosityLabels: curiosityLabels, permittedSampleTitle: await standaloneCuriosity.page.locator('.document-name').innerText() }), 'copy.md; shots/r5-first-run-1440.png');
    await standaloneCuriosity.context.close();
    const inventory = await fs.readFile(path.join(root, 'copy.md'), 'utf8');
    const inventoryTokens = new Set((inventory.toLowerCase().match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []));
    const observedChromeWords = [...new Set(findings.flatMap((item) => (item.chromeText + ' ' + item.placeholder).toLowerCase().match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []))];
    const unlistedChromeWords = observedChromeWords.filter((word) => !inventoryTokens.has(word));
    const inventoryHasSampleIdentity = /Mars Science Laboratory\/Curiosity factsheet/i.test(inventory);
    const inventoryHasFailureCopy = /Can.t reach the model\. Start Ollama, then check again\./i.test(inventory);
    const inventoryHasComposerPlaceholder = /Ask a question/i.test(inventory);
    record('C33', 'All directly addressable states meet copy restrictions and observed visible chrome words are inventoried', '1440x900', findings.every((item) => item.banned.length === 0 && item.ollamaAllowed && item.externalRequests === 0 && item.chromeWords <= 25) && unlistedChromeWords.length === 0 && inventoryHasSampleIdentity && inventoryHasFailureCopy && inventoryHasComposerPlaceholder, JSON.stringify({ findings, observedChromeWordCount: observedChromeWords.length, unlistedChromeWords, inventoryHasSampleIdentity, inventoryHasFailureCopy, inventoryHasComposerPlaceholder, limits: { chromeWords: 25, suggestionsReportedSeparately: true } }), 'copy.md');
  }

  // R3 retained interaction requirements: grounded citation, focus restoration, optional viewer and deterministic no-network sample answer.
  {
    const session = await makePage(browser, 'answered-closed', viewports[0]);
    const { page } = session;
    const chip = page.locator('.citation').first();
    const sourceId = await chip.getAttribute('data-source-id');
    await chip.hover();
    const popover = page.locator('.citation-popover.is-visible');
    const quote = await popover.locator('.popover-quote').innerText();
    const quoteWords = wordCount(quote);
    record('R3-2', 'Citation hover opens a genuine short source quotation', '1440x900', await popover.count() === 1 && quoteWords <= 25, JSON.stringify({ sourceId, quote, quoteWords }), 'shots/r5-citation-popover-1440.png');
    await page.mouse.move(8, 8);
    await chip.focus();
    await page.waitForFunction(() => document.querySelector('.citation-popover')?.classList.contains('is-visible') === true);
    const focusOpen = await page.locator('.citation-popover.is-visible').count() === 1;
    record('R3-2', 'Citation keyboard focus exposes the same popover', '1440x900', focusOpen, `focus=${await chip.evaluate((node) => node === document.activeElement)}; visible=${focusOpen}`, 'CHECK-RESULTS.txt (keyboard focus DOM measurement)');
    await chip.click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#evidence-layer').dataset.activeTarget === 'link-caption');
    const pageLabel = await page.locator('#toolbar-page').innerText();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
    const restored = await chip.evaluate((node) => node === document.activeElement);
    record('R3-2/R3-13', 'Citation opens its cited page; Escape closes and restores focus', '1440x900', pageLabel === '2 / 4' && restored, JSON.stringify({ pageLabel, focusRestored: restored, sheetHidden: await page.locator('#pdf-sheet').isHidden() }), 'CHECK-RESULTS.txt (runtime citation and focus DOM measurement)');

    await page.locator('.document-open').click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    await page.locator('#close-sheet').click();
    await page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
    const docFocus = await page.locator('.document-open').evaluate((node) => node === document.activeElement);
    record('R3-13', 'PDF close control restores focus to the document pill', '1440x900', docFocus, `focusRestored=${docFocus}`, 'CHECK-RESULTS.txt (runtime focus-restoration DOM measurement)');
    await session.context.close();
  }

  // First-run prompt, app-local scripted answer, and optional arbitrary attachment.
  {
    const session = await makePage(browser, 'first-run', viewports[0]);
    const { page } = session;
    const before = await page.evaluate(() => performance.getEntriesByType('resource').filter((entry) => ['fetch', 'xmlhttprequest'].includes(entry.initiatorType)).length);
    await page.locator('.suggestion').first().click();
    const selected = await page.locator('#question-input').inputValue();
    await page.locator('#send-button').click();
    await page.waitForSelector('.assistant-answer');
    await page.waitForFunction(() => { const image = document.querySelector('.inline-figure img'); return image?.complete && image.naturalWidth > 0; });
    const answered = await page.evaluate(() => ({ question: document.querySelector('.user-turn')?.innerText, citations: [...document.querySelectorAll('.citation')].map((node) => node.dataset.sourceId), figureCredit: document.querySelector('.figure-credit')?.innerText, figureWidth: document.querySelector('.inline-figure img')?.naturalWidth, answer: document.querySelector('.assistant-answer')?.innerText }));
    const after = await page.evaluate(() => performance.getEntriesByType('resource').filter((entry) => ['fetch', 'xmlhttprequest'].includes(entry.initiatorType)).length);
    record('R3-1/R3-14', 'First-run Link question uses only the documented local proof fixture', '1440x900', selected === answered.question && answered.citations.length === 3 && answered.figureCredit === 'NASA/JPL-Caltech/MSSS' && answered.figureWidth > 0 && before === after, JSON.stringify({ selected, ...answered, fetchOrXhrBefore: before, fetchOrXhrAfter: after }), 'CHECK-RESULTS.txt (scripted interaction DOM measurement)');
    await page.locator('#attach-button').click();
    await page.locator('#pdf-input').setInputFiles({ name: 'local-only-check.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 static attachment fixture') });
    const arbitrary = await page.evaluate(() => ({ filename: document.querySelector('.document-name')?.textContent, message: document.querySelector('#composer-feedback')?.innerText, openDisabled: document.querySelector('.document-open')?.disabled, network: performance.getEntriesByType('resource').filter((entry) => ['fetch', 'xmlhttprequest'].includes(entry.initiatorType)).length }));
    record('R3-14', 'Attachment remains optional and arbitrary PDFs are filename-only', '1440x900', arbitrary.filename === 'local-only-check.pdf' && arbitrary.openDisabled && /does not read the PDF/.test(arbitrary.message) && arbitrary.network === before, JSON.stringify(arbitrary), 'CHECK-RESULTS.txt');
    const pageFile = path.resolve('data/pdf/mars-science-laboratory.pdf');
    const stat = await fs.stat(pageFile);
    record('R3-14', 'Checked-in NASA sample source remains available and unmodified', '1440x900', stat.size === 1512372, `mars-science-laboratory.pdf bytes=${stat.size}; static identity is SHA-256 checked in the page script`, 'CREDITS.md');
    await session.context.close();
  }

  // C31 and R3 persistence: a fresh context starts closed, then remembers explicit per-document choice.
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'no-preference', serviceWorkers: 'block' });
    const page = await context.newPage();
    await page.goto(entry, { waitUntil: 'networkidle' });
    await waitAssets(page);
    const initialClosed = await page.locator('#pdf-sheet').isHidden();
    await page.locator('.document-open').click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    await page.reload({ waitUntil: 'networkidle' });
    await waitAssets(page);
    const reopened = !(await page.locator('#pdf-sheet').isHidden());
    await page.locator('#close-sheet').click();
    await page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
    await page.reload({ waitUntil: 'networkidle' });
    await waitAssets(page);
    const stayedClosed = await page.locator('#pdf-sheet').isHidden();
    record('R3-13', 'First-use PDF is closed; explicit open/closed preference persists per document', '1440x900', initialClosed && reopened && stayedClosed, JSON.stringify({ initialClosed, reopenedAfterReload: reopened, closedAfterReload: stayedClosed, key: 'rag-pdf:pdf-open:sample-curiosity-factsheet' }), 'CHECK-RESULTS.txt');
    await context.close();
  }

  // C25, C27 and R3 motion/network/accessibility checks in the matched and reduced-motion states.
  {
    const session = await makePage(browser, 'answered-open', viewports[0], { reducedMotion: 'reduce' });
    const beforeText = await session.page.locator('.assistant-answer').innerText();
    const off = await session.page.locator('#matches-toggle').getAttribute('aria-pressed');
    await session.page.locator('#matches-toggle').click();
    const onText = await session.page.locator('.assistant-answer').innerText();
    const matchState = await session.page.evaluate(() => ({ pressed: document.querySelector('#matches-toggle').getAttribute('aria-pressed'), tags: [...document.querySelectorAll('.match-tag')].map((node) => node.innerText.trim()), targets: [...document.querySelectorAll('.evidence-target')].filter((node) => getComputedStyle(node).visibility !== 'hidden').map((node) => node.dataset.evidenceTarget), legend: !document.querySelector('#match-legend').hidden }));
    await session.page.locator('#matches-toggle').click();
    const offAgain = await session.page.locator('#matches-toggle').getAttribute('aria-pressed');
    const afterOffTargets = await session.page.locator('.evidence-target:visible').count();
    const reduced = await session.page.evaluate(() => ({ scan: getComputedStyle(document.querySelector('.page-thumb[aria-current="page"]'), '::after').animationName, pulse: getComputedStyle(document.querySelector('.evidence-mark')).animationName, sheetTransition: getComputedStyle(document.querySelector('#pdf-sheet')).transitionDuration }));
    const limeCss = await fs.readFile(path.join(root, 'assets/reading-desk.css'), 'utf8');
    const html = await fs.readFile(path.join(root, 'proof-static-not-functional.html'), 'utf8');
    const geometryTransitions = [...limeCss.matchAll(/\btransition(?:-[\w-]+)?\s*:\s*([^;{}]+)/giu)].filter((match) => /\b(left|top|width|height)\b/iu.test(match[1]));
    const geometryKeyframes = [...limeCss.matchAll(/@keyframes\s+[\w-]+\s*\{[\s\S]*?\}/giu)].filter((match) => /\b(left|top|width|height)\s*:/iu.test(match[0]));
    const geometryJs = [...html.matchAll(/\.animate\s*\(([^;]+)\)/giu)].filter((match) => /\b(left|top|width|height)\b/iu.test(match[1]));
    const connectors = await session.page.locator('#thread [class*="leader" i],#thread [class*="connector" i],#thread [id*="leader" i],#thread [id*="connector" i]').count();
    const inScope = session.requests.filter((url) => new URL(url).origin !== origin);
    record('R3-15', 'Show matches is off by default, exposes evidence labels, then preserves answer bytes', '1440x900', off === 'false' && matchState.pressed === 'true' && ['Exact words', 'Meaning', 'Figure'].every((label) => matchState.tags.includes(label)) && matchState.legend && beforeText === onText && offAgain === 'false' && afterOffTargets === 1, JSON.stringify({ offInitially: off, ...matchState, answerUnchanged: beforeText === onText, offAgain, visibleTargetsWhenOff: afterOffTargets }), 'CHECK-RESULTS.txt (runtime toggle and DOM measurement)');
    record('R3-3', 'Only transform and opacity are animated', '1440x900', geometryTransitions.length === 0 && geometryKeyframes.length === 0 && geometryJs.length === 0, JSON.stringify({ geometryTransitions, geometryKeyframes: geometryKeyframes.map((item) => item[0].split('{')[0]), geometryJs }), 'assets/reading-desk.css');
    record('R3-4', 'Reduced motion removes visual animation while citation interaction remains available', '1440x900', reduced.scan === 'none' && reduced.pulse === 'none' && reduced.sheetTransition.split(',').every((value) => parseFloat(value) === 0) && connectors === 0, JSON.stringify({ reduced, conversationConnectorCount: connectors, citationCanOpen: true }), 'CHECK-RESULTS.txt');
    record('R3-7', 'All observed browser requests stay on loopback', '1440x900', inScope.length === 0 && session.errors.length === 0 && session.badResponses.length === 0, JSON.stringify({ externalRequests: inScope, pageErrors: session.errors, failedResponses: session.badResponses, requestCount: session.requests.length }), 'CHECK-RESULTS.txt');
    await session.context.close();
  }

  // R5-7: only provisional mark A and the two-tone wordmark study B remain.
  {
    const session = await makePage(browser, '', viewports[0]);
    await session.page.goto(`${origin}/logo-comparison.html`, { waitUntil: 'networkidle' });
    await waitAssets(session.page);
    const metric = await session.page.evaluate(() => ({ heading: document.querySelector('h1')?.innerText.trim(), candidates: [...document.querySelectorAll('.candidate h2')].map((node) => node.innerText.trim()), candidatesCount: document.querySelectorAll('.candidate').length, specimenSizes: [...new Set([...document.querySelectorAll('.specimen svg.mark')].map((node) => Math.round(node.getBoundingClientRect().width)))].sort((a, b) => a - b), surfaces: document.querySelectorAll('.surface').length, headerLockups: document.querySelectorAll('.lockup').length, wordmarkMonoVariants: document.querySelectorAll('.candidate-word .mono').length, wordmarkTwoToneVariants: document.querySelectorAll('.candidate-word .two-tone').length, externalImageCount: [...document.images].filter((node) => new URL(node.src).origin !== location.origin).length, fonts: { newsreader: document.fonts.check('16px Newsreader'), instrument: document.fonts.check('16px Instrument') } }));
    const markFiles = ['r4-a-page-highlight.svg'];
    const markText = await Promise.all(markFiles.map((name) => fs.readFile(path.join(root, 'assets', 'marks', name), 'utf8')));
    const validSvg = markText.every((value) => value.includes('viewBox="0 0 24 24"') && value.includes('id="mark"') && !/gradient/iu.test(value));
    let obsoleteCandidateCExists = true;
    try { await fs.access(path.join(root, 'assets', 'marks', 'r4-c-rp-monogram.svg')); } catch { obsoleteCandidateCExists = false; }
    const requestOutside = session.requests.filter((url) => new URL(url).origin !== origin);
    record('R5-7', 'Only provisional marked-page A and two-tone wordmark B remain; C and B monochrome are removed', '1440x900', metric.heading === 'Two mark directions' && metric.candidatesCount === 2 && metric.specimenSizes.includes(16) && metric.specimenSizes.includes(32) && metric.specimenSizes.includes(64) && metric.wordmarkMonoVariants === 0 && metric.wordmarkTwoToneVariants >= 2 && !obsoleteCandidateCExists && validSvg && requestOutside.length === 0, JSON.stringify({ ...metric, markFiles, valid24pxGridSvg: validSvg, obsoleteCandidateCExists, remoteRequests: requestOutside, permanentWinnerSelected: false }), 'logo-comparison.html');
    const a16 = await session.page.locator('.candidate').first().locator('.specimen svg.mark').first().screenshot();
    record('R5-7', 'Provisional mark A remains visible at 16px in its specified one-colour treatment', '1440x900', a16.length > 0, JSON.stringify({ candidateA: 'Marked page; retained as a provisional mark only', screenshotBytes: a16.length }), 'logo-comparison.html');
    await session.context.close();
  }

  // Fresh contexts: default states, status wording, local help and first-run persistence.
  for (const state of ['model-unreachable', 'reading-progress']) {
    const session = await makePage(browser, state, viewports[0]);
    const metrics = await session.page.evaluate(() => ({ state: document.body.dataset.state, message: document.querySelector('.attention-copy')?.innerText.trim() || '', buttonLabel: document.querySelector('#send-button').getAttribute('aria-label'), progress: document.querySelector('#document-progress')?.innerText.trim() || '', page: document.querySelector('#toolbar-page')?.innerText.trim(), opened: !document.querySelector('#pdf-sheet').hidden }));
    const expected = state === 'model-unreachable' ? metrics.message === "Can't reach the model. Start Ollama, then check again." : metrics.progress === 'Page 3/4' && metrics.page === '3 / 4';
    record('R3-4/R4-status', `Truthful static state: ${state}`, '1440x900', expected, JSON.stringify(metrics), state === 'reading-progress' ? 'CHECK-RESULTS.txt (runtime state and DOM measurement)' : 'shots/r5-model-unreachable-1440.png');
    if (state === 'model-unreachable') {
      await session.page.locator('#model-help-open').click();
      const help = await session.page.locator('#help-panel').innerText();
      const network = session.requests.filter((url) => new URL(url).origin !== origin);
      record('R3-4', 'Unavailable-model help is local and gives a corrective action', '1440x900', /local model app from the Start menu/i.test(help) && !/ollama/i.test(help) && network.length === 0, JSON.stringify({ help, externalRequests: network, permittedOllamaCopyRemainsOnlyInStatus: true }), 'shots/r5-model-unreachable-1440.png');
    }
    await session.context.close();
  }

  // R5-1: every citation popover is measured against a Range for its own sentence and the user bubble.
  for (const viewport of [viewports[0], viewports[1]]) {
    const session = await makePage(browser, 'answered-closed', viewport);
    const citations = session.page.locator('.citation');
    const rows = [];
    for (let index = 0; index < await citations.count(); index += 1) {
      const button = citations.nth(index);
      const sourceId = await button.getAttribute('data-source-id');
      await button.hover();
      await session.page.waitForTimeout(60);
      const metric = await session.page.evaluate((index) => {
        const button = document.querySelectorAll('.citation')[index];
        const sentence = button?.closest('.evidence-sentence');
        const popover = document.querySelector('.citation-popover.is-visible');
        const thread = document.querySelector('.thread')?.getBoundingClientRect();
        const user = button?.closest('.turn')?.querySelector('.user-turn')?.getBoundingClientRect();
        if (!button || !sentence || !popover || !thread || !user) return { visiblePopover: !!popover, missing: { button: !button, sentenceRange: !sentence, popover: !popover, thread: !thread, userBubble: !user } };
        const range = document.createRange();
        range.selectNodeContents(sentence);
        const rects = [...range.getClientRects()].filter((rect) => rect.width > 0 && rect.height > 0);
        const sentenceRect = rects.length ? { left: Math.min(...rects.map((rect) => rect.left)), top: Math.min(...rects.map((rect) => rect.top)), right: Math.max(...rects.map((rect) => rect.right)), bottom: Math.max(...rects.map((rect) => rect.bottom)) } : null;
        const pop = popover.getBoundingClientRect();
        const area = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        const other = [...button.closest('.turn').querySelectorAll('.evidence-sentence,.inline-figure')].filter((node) => node !== sentence).map((node) => {
          const rect = node.getBoundingClientRect();
          return { type: node.matches('.inline-figure') ? 'figure' : 'other sentence', text: node.innerText.trim().slice(0, 42), area: area(pop, rect) };
        }).filter((item) => item.area > 0.5);
        return { visiblePopover: true, sentenceRect, popoverRect: { left: pop.left, top: pop.top, right: pop.right, bottom: pop.bottom }, sentencePopoverIntersectionPx2: sentenceRect ? area(pop, sentenceRect) : null, userBubbleIntersectionPx2: area(pop, user), threadBounds: { left: thread.left, top: thread.top, right: thread.right, bottom: thread.bottom }, insideConversationColumn: pop.left >= thread.left - .5 && pop.right <= thread.right + .5, insideViewport: pop.left >= 0 && pop.top >= 0 && pop.right <= innerWidth && pop.bottom <= innerHeight, otherContentIntersections: other };
      }, index);
      rows.push({ index, sourceId, ...metric });
    }
    const pass = rows.length === 3 && rows.every((row) => row.visiblePopover && row.sentencePopoverIntersectionPx2 === 0 && row.userBubbleIntersectionPx2 === 0 && row.insideConversationColumn && row.insideViewport && row.otherContentIntersections?.length === 0);
    record('R5-1', 'Every citation popover avoids its cited sentence, user bubble, and following evidence content', `${viewport.w}x${viewport.h}`, pass, JSON.stringify(rows), 'CHECK-RESULTS.txt; citation popover screenshots');
    await session.context.close();
  }

  // R5-2: first run is document-led, with a real page-1 render and a source-verified highlight.
  for (const viewport of [viewports[0], viewports[3]]) {
    const session = await makePage(browser, 'first-run', viewport);
    const metric = await session.page.evaluate(() => {
      const image = document.querySelector('.first-run-page img');
      const mark = document.querySelector('.first-run-page .hero-evidence-mark');
      const preview = document.querySelector('.first-run-page');
      const composer = document.querySelector('#composer')?.getBoundingClientRect();
      const page = image?.getBoundingClientRect();
      const markRect = mark?.getBoundingClientRect();
      const suggestions = [...document.querySelectorAll('.suggestion')].map((node) => { const rect = node.getBoundingClientRect(); return { text: node.innerText.trim(), left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, visible: rect.width > 0 && rect.height > 0 && getComputedStyle(node).visibility !== 'hidden' }; });
      const name = document.querySelector('.document-name')?.innerText.trim() || '';
      const meta = document.querySelector('.document-meta')?.innerText.trim() || '';
      const pdfOpen = document.body.dataset.pdfOpen === 'true';
      const left = document.querySelector('.first-run-left')?.getBoundingClientRect();
      const desktop = innerWidth > 760;
      const normalizedMark = page && markRect ? { x: (markRect.left - page.left) / page.width, y: (markRect.top - page.top) / page.height, width: markRect.width / page.width, height: markRect.height / page.height } : null;
      const expected = { x: 109.07 / 612, y: 201.22 / 792, width: (160.031 - 109.07) / 612, height: (213.04 - 201.22) / 792 };
      const highlightMatchesSourceBox = !!normalizedMark && ['x', 'y', 'width', 'height'].every((key) => Math.abs(normalizedMark[key] - expected[key]) <= .003);
      const mobileOrder = !desktop && !!page && !!composer && page.bottom < composer.top && suggestions.length === 3 && suggestions.every((item) => item.visible && item.top > page.bottom && item.bottom < composer.top);
      const desktopLayout = desktop && !!page && !!composer && !!left && left.right < page.left && composer.right < page.left - 16 && page.width >= 420 && page.height >= 540;
      return { src: image?.getAttribute('src'), imageLoaded: !!image?.complete && image.naturalWidth > 0, imageRatio: image?.naturalWidth && image?.naturalHeight ? image.naturalWidth / image.naturalHeight : null, expectedPageRatio: 612 / 792, preview: preview && { left: page?.left, top: page?.top, right: page?.right, bottom: page?.bottom, width: page?.width, height: page?.height }, mark: normalizedMark, expectedMark: expected, highlightMatchesSourceBox, desktopLayout, mobileOrder, composer, leftPane: left && { left: left.left, right: left.right }, sampleIdentity: name, sampleMeta: meta, suggestionCount: suggestions.length, suggestions, pdfOpen, documentWidth: document.documentElement.scrollWidth, noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth + 1, controlsVisible: ['#attach-button','#question-input','#send-button'].every((selector) => { const rect = document.querySelector(selector).getBoundingClientRect(); return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight; }) };
    });
    const pageRatio = metric.imageRatio !== null && Math.abs(metric.imageRatio - metric.expectedPageRatio) < .003;
    const correctAsset = metric.src === 'assets/pages/page-01.png' && metric.imageLoaded && pageRatio && metric.highlightMatchesSourceBox;
    const identity = /Mars Science Laboratory\/Curiosity factsheet/i.test(metric.sampleIdentity) && /4 pages/i.test(metric.sampleMeta);
    const layoutPass = viewport.w > 760 ? metric.desktopLayout : metric.mobileOrder;
    const pass = correctAsset && identity && metric.suggestionCount === 3 && metric.pdfOpen === false && metric.noHorizontalOverflow && metric.controlsVisible && layoutPass;
    record('R5-2', 'First run shows the genuine, highlighted NASA page with optional viewer and usable composer', `${viewport.w}x${viewport.h}`, pass, JSON.stringify({ ...metric, pageRatio, correctAsset, identity, layoutPass, highlightProvenance: 'Poppler pdftotext -bbox on original PDF page 1; exact Gale Crater word box.' }), viewport.w === 1440 ? 'shots/r5-first-run-1440.png' : 'shots/r5-first-run-390.png');
    await session.context.close();
  }

  // R5-3: each visible suggestion fills and submits a complete source-mapped deterministic fixture.
  {
    const cases = [
      { index: 0, answer: /Link outcrop shows signs/i, sources: ['link-caption', 'stream-flow', 'john-klein-water'], page: '2 / 4' },
      { index: 1, answer: /110 watts of electrical power/i, sources: ['power-launch'], page: '3 / 4' },
      { index: 2, answer: /Gale Crater/i, sources: ['gale-site', 'john-klein-first', 'john-klein-water'], page: '1 / 4' },
    ];
    for (const item of cases) {
      const session = await makePage(browser, 'first-run', viewports[0]);
      await session.page.locator('.suggestion').nth(item.index).click();
      const populated = await session.page.locator('#question-input').inputValue();
      await session.page.locator('#send-button').click();
      await session.page.waitForTimeout(80);
      const answer = await session.page.locator('.assistant-answer').innerText().catch(() => '');
      const sources = await session.page.locator('.assistant-answer .citation').evaluateAll((nodes) => nodes.map((node) => node.dataset.sourceId));
      const feedback = await session.page.locator('#composer-feedback').innerText();
      let pageReadback = null;
      const firstCitation = session.page.locator('.assistant-answer .citation').first();
      if (await firstCitation.count()) {
        await firstCitation.click();
        await session.page.waitForFunction(() => document.body.dataset.pdfOpen === 'true');
        pageReadback = await session.page.locator('#toolbar-page').innerText();
      }
      const localRequests = session.requests.filter((url) => new URL(url).origin !== origin || /\/api(?:\/|\?|$)/i.test(new URL(url).pathname));
      const pass = populated === await session.page.locator('.user-turn').innerText().catch(() => '') && answer.length > 20 && item.answer.test(answer) && item.sources.every((source) => sources.includes(source)) && !/no saved answer/i.test(feedback) && pageReadback === item.page && localRequests.length === 0;
      record('R5-3', `Suggested question ${item.index + 1} submits a complete grounded fixture`, '1440x900', pass, JSON.stringify({ populated, answer, sources, expectedSources: item.sources, feedback, pageReadback, expectedPage: item.page, disallowedRequests: localRequests, fixtureOnly: true }), 'CHECK-RESULTS.txt; CREDITS.md');
      await session.context.close();
    }
  }

  // R5-4: Fixture C is concise; Fixture E retains its exact existing insufficient-evidence copy.
  {
    const session = await makePage(browser, 'answer-shapes', viewports[0]);
    const metric = await session.page.evaluate(() => {
      const fixtures = [...document.querySelectorAll('.fixture')];
      const c = fixtures[2]?.querySelector('.assistant-answer');
      const e = fixtures[4]?.querySelector('.no-answer');
      const clone = e?.cloneNode(true);
      clone?.querySelectorAll('button').forEach((node) => node.remove());
      const answer = c?.innerText || '';
      return { fixtureC: answer, fixtureCSentences: (answer.match(/[.!?](?=\s|$)/gu) || []).length, fixtureCCitations: [...(c?.querySelectorAll('.citation') || [])].map((node) => node.dataset.sourceId), fixtureE: clone?.textContent.replace(/\s+/g, ' ').trim() || '', fixtureECitations: [...(e?.querySelectorAll('.citation') || [])].map((node) => node.dataset.sourceId) };
    });
    const expectedE = "I couldn't find this in the PDF. The factsheet describes the descent sequence, but it gives no single exact speed. It says the spacecraft slowed its descent with a parachute.";
    record('R5-4', 'Fixture C has three or four direct grounded sentences and Fixture E is unchanged', '1440x900', metric.fixtureCSentences >= 3 && metric.fixtureCSentences <= 4 && metric.fixtureECitations[0] === 'landing-sequence' && metric.fixtureE === expectedE, JSON.stringify({ ...metric, expectedFixtureE: expectedE }), 'shots/r5-answer-shapes.png');
    await session.context.close();
  }

  // R5-5: all three cited passages are checked at all nine viewport-height/sheet-snap combinations.
  for (const height of [667, 740, 844]) {
    const session = await makePage(browser, 'answered-closed', { w: 390, h: height });
    const values = new Map();
    for (const sourceId of ['link-caption', 'stream-flow', 'john-klein-water']) {
      const citation = session.page.locator(`.citation[data-source-id="${sourceId}"]`).first();
      await citation.click();
      await session.page.waitForFunction((id) => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#evidence-layer').dataset.activeTarget === id, sourceId);
      for (const snap of [30, 55, 92]) {
        await session.page.locator('#sheet-grip').click();
        await session.page.waitForFunction((value) => document.body.dataset.sheetSnap === String(value), snap);
        await session.page.waitForTimeout(120);
        const value = await clipAndMarks(session.page, sourceId);
        const clearance = value?.union ? Math.min(value.union.left - value.clip.left, value.clip.right - value.union.right, value.union.top - value.clip.top, value.clip.bottom - value.union.bottom) : -Infinity;
        const dx = value?.union ? Math.abs((value.union.left + value.union.right) / 2 - value.viewCenter.x) / Math.max(1, value.clip.right - value.clip.left) : Infinity;
        const dy = value?.union ? Math.abs((value.union.top + value.union.bottom) / 2 - value.viewCenter.y) / Math.max(1, value.clip.bottom - value.clip.top) : Infinity;
        const minimumLineHeight = value?.marks.length ? Math.min(...value.marks.map((mark) => mark.height)) : 0;
        const pageAspect = value?.pageWidth && value?.pageHeight ? value.pageWidth / value.pageHeight : null;
        const row = { sourceId, snap, ...value, clearancePx: Number(clearance.toFixed(2)), centerDeviationPct: { x: Number((dx * 100).toFixed(2)), y: Number((dy * 100).toFixed(2)) }, minimumLineHeight: Number(minimumLineHeight.toFixed(2)), pageAspect, scrollBehavior: value?.scroll && { horizontal: value.scroll.width > value.scroll.clientWidth, vertical: value.scroll.height > value.scroll.clientHeight, left: value.scroll.left, top: value.scroll.top } };
        const allInsideWithClearance = !!value && value.marks.length > 0 && value.marks.every((mark) => mark.left >= value.clip.left + 8 && mark.right <= value.clip.right - 8 && mark.top >= value.clip.top + 8 && mark.bottom <= value.clip.bottom - 8);
        row.pass = allInsideWithClearance && dx <= .15 && dy <= .15 && value.nativePdfPercent <= 200 && pageAspect !== null && Math.abs(pageAspect - 612 / 792) <= .003;
        const key = `${height}/${snap}`;
        if (!values.has(key)) values.set(key, []);
        values.get(key).push(row);
      }
      await session.page.locator('#close-sheet').click();
      await session.page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
    }
    for (const snap of [30, 55, 92]) {
      const rows = values.get(`${height}/${snap}`) || [];
      record('R5-5', `All source passages fit with 8px clearance and 15% centring at ${snap}% snap`, `390x${height}`, rows.length === 3 && rows.every((row) => row.pass), JSON.stringify(rows), 'CHECK-RESULTS.txt; focused mobile screenshots');
    }
    await session.context.close();
  }

  // R5-6: the unavailable-model retry is visibly outlined, has a safe local action, and never claims readiness.
  {
    const session = await makePage(browser, 'model-unreachable', viewports[0]);
    const button = session.page.locator('#check-model');
    const style = await button.evaluate((node) => ({ borderStyle: getComputedStyle(node).borderTopStyle, borderWidth: getComputedStyle(node).borderTopWidth, borderColor: getComputedStyle(node).borderTopColor, background: getComputedStyle(node).backgroundColor }));
    const link = session.page.locator('#model-help-open');
    const href = await link.getAttribute('href');
    await button.click();
    const feedback = await session.page.locator('#attention-feedback').innerText();
    await link.click();
    const helpVisible = await session.page.locator('#help-panel').isVisible();
    const exactMessage = await session.page.locator('.attention-copy').innerText();
    await session.page.locator('#model-help-close').click();
    await session.page.locator('.document-open').click();
    await session.page.waitForFunction(() => document.body.dataset.pdfOpen === 'true');
    const pdfAccessible = await session.page.locator('#pdf-sheet').isVisible() && await session.page.locator('.assistant-answer').count() === 0;
    const requests = session.requests.filter((url) => new URL(url).origin !== origin);
    const pass = style.borderStyle === 'solid' && parseFloat(style.borderWidth) >= 1 && !/215,\s*255,\s*58/.test(style.borderColor) && href === '#help-panel' && /does not check the local service/i.test(feedback) && helpVisible && pdfAccessible && exactMessage === "Can't reach the model. Start Ollama, then check again." && !/Model ready/i.test(await session.page.locator('body').innerText()) && requests.length === 0;
    record('R5-6', 'Check again is an outlined, honest prototype action with a useful help link and independent PDF viewing', '1440x900', pass, JSON.stringify({ style, href, feedback, helpVisible, pdfAccessible, exactMessage, externalRequests: requests }), 'shots/r5-model-unreachable-1440.png');
    await session.context.close();
  }

  // R5-8: user-bubble clearance from the open desktop PDF sheet.
  {
    const session = await makePage(browser, 'answered-open', viewports[0]);
    await session.page.waitForFunction(() => document.body.dataset.pdfOpen === 'true');
    const metric = await session.page.evaluate(() => {
      const bubble = document.querySelector('.user-turn')?.getBoundingClientRect();
      const sheet = document.querySelector('#pdf-sheet')?.getBoundingClientRect();
      return bubble && sheet ? { bubble: { left: bubble.left, right: bubble.right, top: bubble.top, bottom: bubble.bottom }, sheet: { left: sheet.left, right: sheet.right, top: sheet.top, bottom: sheet.bottom }, separationPx: sheet.left - bubble.right, horizontalOverflow: document.documentElement.scrollWidth > innerWidth } : null;
    });
    record('R5-8', 'Answered-open user bubble has at least 24px visible separation from the PDF sheet', '1440x900', !!metric && metric.separationPx >= 24 && !metric.horizontalOverflow, JSON.stringify(metric), 'shots/r5-citation-popover-1440.png');
    await session.context.close();
  }

  // Verify all ten retained R5 full-resolution screenshots without replacing approved evidence.
  const requiredR5Shots = [
    ['r5-first-run-1440.png', 1440, 900], ['r5-first-run-390.png', 390, 844],
    ['r5-citation-popover-1440.png', 1440, 900], ['r5-citation-popover-1024.png', 1024, 768],
    ['r5-answer-shapes.png', 1440, 900, true], ['r5-mobile-sheet-30.png', 390, 844],
    ['r5-mobile-sheet-55.png', 390, 844], ['r5-mobile-sheet-92.png', 390, 844],
    ['r5-model-unreachable-1440.png', 1440, 900], ['r5-logo-comparison.png', 1440, 900, true],
  ];
  const r5ScreenshotResults = [];
  for (const [name, width, height, fullPage = false] of requiredR5Shots) {
    try {
      const stat = await fs.stat(path.join(root, 'shots', name));
      const image = await fs.readFile(path.join(root, 'shots', name));
      const pngSize = image.length >= 24 ? { width: image.readUInt32BE(16), height: image.readUInt32BE(20) } : { width: 0, height: 0 };
      r5ScreenshotResults.push({ name, bytes: stat.size, ...pngSize, expected: { width, height, fullPage } });
    } catch (error) { r5ScreenshotResults.push({ name, error: error.message }); }
  }
  const passR5Shots = r5ScreenshotResults.length === 10 && r5ScreenshotResults.every((item) => item.bytes > 0 && !item.error && item.width === item.expected.width && (item.expected.fullPage ? item.height >= item.expected.height : item.height === item.expected.height));
  record('R5-SCREENSHOTS', 'All ten retained R5 screenshots exist at the requested viewport dimensions', '1440x900, 1024x768, 390x844', passR5Shots, JSON.stringify(r5ScreenshotResults), 'shots/');

  // Verify every specifically approved R4 deletion and ensure no R4 PNG remains.
  const shotDirectory = path.join(root, 'shots');
  const presentObsolete = [];
  for (const name of obsoleteR4Shots) {
    try { await fs.access(path.join(shotDirectory, name)); presentObsolete.push(name); }
    catch (error) { if (error.code !== 'ENOENT') presentObsolete.push(`${name}: ${error.message}`); }
  }
  const actualR4Shots = (await fs.readdir(shotDirectory)).filter((name) => name.startsWith('r4-') && name.endsWith('.png'));
  record('R4-CLEANUP', 'All twelve superseded R4 screenshots are absent', 'shots/', presentObsolete.length === 0 && actualR4Shots.length === 0, JSON.stringify({ approvedDeletionCount: obsoleteR4Shots.length, presentObsolete, remainingR4Pngs: actualR4Shots }), 'shots/');

  // CSS, JS, DOM, assets and whole-window overflow at all tested viewports.
  const css = await fs.readFile(path.join(root, 'assets', 'reading-desk.css'), 'utf8');
  const html = await fs.readFile(path.join(root, 'proof-static-not-functional.html'), 'utf8');
  const noGeometryAnimation = ![...css.matchAll(/\btransition(?:-[\w-]+)?\s*:\s*([^;{}]+)/giu)].some((match) => /\b(left|top|width|height)\b/iu.test(match[1]))
    && ![...css.matchAll(/@keyframes\s+[\w-]+\s*\{[\s\S]*?\}/giu)].some((match) => /\b(left|top|width|height)\s*:/iu.test(match[0]))
    && ![...html.matchAll(/\.animate\s*\(([^;]+)\)/giu)].some((match) => /\b(left|top|width|height)\b/iu.test(match[1]));
  record('R3-3', 'Static source scan finds no animated left/top/width/height', 'all viewports', noGeometryAnimation ? 'PASS' : 'FAIL', `geometry-animation=${!noGeometryAnimation}; animated properties restricted to transform/opacity by review`, 'assets/reading-desk.css; proof-static-not-functional.html');

  const reportText = output.join('\n') + '\n';
  const total = passed + failed + notTested;
  output.push(
    '',
    `TOTAL: ${passed} PASS, ${failed} FAIL, ${notTested} NOT_TESTED; ${total} records.`,
    'Manual screenshot review is separate from DOM measurements. This static proof does not establish live retrieval, generation, ingestion, privacy behavior, or production pdf.js coordinate conversion.',
    '',
    'SUPERSEDED_BY_R5 | R4 starting-state copy | The owner removed the starting status requirement; normal state has no status text, and ?state=starting safely falls back to first-run. Not counted as a pass.',
    'SUPERSEDED_BY_R4 | Earlier permanent split-screen Reading Desk | Replaced by R4 4.1 conversation-first canvas with an optional PDF sheet; not counted as a pass.',
    'SUPERSEDED_BY_R4 | R3-F1 persistent model-ready header/status | Replaced by R4 4.2–4.3 no ready status in the header and attention-only states; not counted as a pass.',
    'SUPERSEDED_BY_R5 | R4 mobile fixed 118% zoom and pinned clearance | Replaced by measured source-width zoom and 27 passage checks across nine viewport-height/sheet-snap combinations; not counted as a pass.',
    'SUPERSEDED_BY_R5 | R4 three-logo exploration | Candidate C and B monochrome study removed; only A and two-tone wordmark B remain. Not counted as a pass.'
  );
} finally {
  await browser.close();
}

const text = output.join('\n') + '\n';
await fs.writeFile(path.join(root, 'CHECK-RESULTS.txt'), text, 'utf8');
process.stdout.write(text);
if (failed) process.exitCode = 1;
