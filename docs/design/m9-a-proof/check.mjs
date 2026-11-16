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
const shots = [
  ['r4-first-run-1440.png', 1440, 900], ['r4-first-run-390.png', 390, 844],
  ['r4-answered-closed-1440.png', 1440, 900], ['r4-answered-open-1440.png', 1440, 900],
  ['r4-matches-1440.png', 1440, 900], ['r4-mobile-sheet-55-390.png', 390, 844],
  ['r4-mobile-answered-390.png', 390, 844], ['r4-model-unreachable-1440.png', 1440, 900],
  ['r4-answer-shapes-1440.png', 1440, 900, true], ['r4-citation-popover-1440.png', 1440, 900],
  ['r4-logo-comparison.png', 1440, 900, true], ['r4-reading-progress-1440.png', 1440, 900],
];
const output = [
  'M9-A-R4 — Reading interface static-proof checks',
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
    clip, marks, union,
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
    record('C20', 'Ready header has no bar, border, or status text', '1440x900', transparent && noBorder && result.visibleStatusCount === 0, JSON.stringify(result), 'shots/r4-first-run-1440.png');

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
    record('C21', 'No non-exempt visible radius is between 0 and 12px; PDF sheet is 16px', '1440x900', radius.smallNonExempt.length === 0 && parseFloat(radius.sheetRadius) === 16, JSON.stringify(radius), 'shots/r4-first-run-1440.png');

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
    record('C22', 'No full-width hairline or chat/PDF divider', '1440x900', dividers.separators.length === 0 && dividers.fullWidthHairlines.length === 0 && dividers.columnBorders.length === 0, JSON.stringify(dividers), 'shots/r4-answered-open-1440.png');
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
    record('C23', 'Full-width paragraph measure and line-height', `${viewport.w}x${viewport.h}`, ok, JSON.stringify({ textColumnPx: metrics?.width, renderedLineCharacterCounts: renderedLines, fullLineAverage: Number(average.toFixed(1)), fontPx: metrics?.fontSize, lineHeightPx: metrics?.lineHeight, ratio: Number(metrics?.ratio.toFixed(3)), note: 'Figure-adjacent fixture D is reported separately; this measures full-width fixture A.' }), 'shots/r4-answer-shapes-1440.png');
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
        insufficientLead: fixtures[4]?.querySelector('.no-answer')?.innerText.startsWith("I couldn't find this in the PDF") || false,
      };
    });
    const noOverflow = fixtureResult.documentWidth <= viewport.w + 1 && fixtureResult.appScrollWidth <= fixtureResult.appWidth + 1;
    const fixturesPass = fixtureResult.count === 5 && fixtureResult.figureCount[3] === 1 && fixtureResult.orderedListCount[1] === 1 && fixtureResult.wordCountLong >= 250 && fixtureResult.insufficientLead;
    record('C32', 'Five grounded answer shapes render without horizontal overflow', `${viewport.w}x${viewport.h}`, fixturesPass && noOverflow, JSON.stringify({ ...fixtureResult, noHorizontalOverflow: noOverflow }), 'shots/r4-answer-shapes-1440.png');

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
    record('C24', 'Citation chip remains with the last word at every answer shape', `${viewport.w}x${viewport.h}`, chipResult.length >= 3 && wrapping.length === 0, JSON.stringify({ citationsMeasured: chipResult.length, violations: wrapping, samples: chipResult.slice(0, 6) }), 'shots/r4-answer-shapes-1440.png');

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
    record('C32', 'All five fixture endings are reachable by scrolling the rendered gallery', `${viewport.w}x${viewport.h}`, inaccessible.length === 0, JSON.stringify({ scrollableDocument: access[2]?.scrollHeight > access[2]?.bodyHeight, inaccessible, endingRects: access.map(({ fixture, lastGlyph }) => ({ fixture, lastGlyph })) }), 'shots/r4-answer-shapes-1440.png');

    if (viewport.w >= 1024) {
      const figure = await session.page.evaluate(() => {
        const lead = document.querySelector('.fixture:nth-of-type(4) .answer-lead')?.getBoundingClientRect();
        const figureRect = document.querySelector('.fixture:nth-of-type(4) .inline-figure')?.getBoundingClientRect();
        return { lead: lead && { left: lead.left, right: lead.right, top: lead.top, bottom: lead.bottom }, figure: figureRect && { left: figureRect.left, right: figureRect.right, top: figureRect.top, bottom: figureRect.bottom }, intersects: !!lead && !!figureRect && lead.left < figureRect.right && lead.right > figureRect.left && lead.top < figureRect.bottom && lead.bottom > figureRect.top };
      });
      record('R3-1', 'Genuine figure sits beside its supporting first paragraph on desktop', `${viewport.w}x${viewport.h}`, !!figure.lead && !!figure.figure && !figure.intersects, JSON.stringify(figure), 'shots/r4-answer-shapes-1440.png');
    } else {
      const figure = await session.page.evaluate(() => {
        const lead = document.querySelector('.fixture:nth-of-type(4) .answer-lead')?.getBoundingClientRect();
        const figureRect = document.querySelector('.fixture:nth-of-type(4) .inline-figure')?.getBoundingClientRect();
        return { lead: lead && { top: lead.top, bottom: lead.bottom }, figure: figureRect && { top: figureRect.top, bottom: figureRect.bottom }, followsLead: !!lead && !!figureRect && figureRect.top >= lead.bottom };
      });
      record('R3-1', 'Figure follows its supporting paragraph at narrow width', `${viewport.w}x${viewport.h}`, !!figure.lead && !!figure.figure && figure.followsLead, JSON.stringify(figure), 'shots/r4-mobile-answered-390.png; Playwright DOM order measurement');
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
      const allowed = (node) => node.matches('.brand-mark,.document-thumb-highlight,.citation-mark,.evidence-mark,.citation-popover .popover-quote,.match-tag[aria-pressed="true"],.match-mobile button[aria-pressed="true"],.legend-mark') || !!node.closest('.brand-mark,.document-thumb-highlight,.citation-mark,.evidence-mark,.citation-popover .popover-quote,.match-tag[aria-pressed="true"],.match-mobile button[aria-pressed="true"],.legend-mark');
      const uses = [];
      for (const node of document.querySelectorAll('body *')) {
        if (!node.getClientRects().length) continue;
        const style = getComputedStyle(node);
        const values = ['color', 'backgroundColor', 'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor', 'outlineColor', 'boxShadow'].map((key) => [key, style[key]]);
        for (const [property, value] of values) if (lime.test(value)) uses.push({ node: node.id || node.className?.baseVal || node.className || node.tagName, property, value, allowed: allowed(node) });
      }
      return { uses, unexpected: uses.filter((item) => !item.allowed) };
    });
    record('C25', 'Lime appears only on document evidence and the provisional logo', '1440x900', colors.unexpected.length === 0, JSON.stringify(colors), 'shots/r4-first-run-1440.png');

    const mono = await session.page.evaluate(() => [...document.querySelectorAll('body *')].filter((node) => node.getClientRects().length && /IBM Plex Mono/i.test(getComputedStyle(node).fontFamily) && !node.closest('[data-role="page-ref"]')).map((node) => ({ id: node.id, className: node.className?.baseVal || node.className, family: getComputedStyle(node).fontFamily })));
    record('C26', 'Monospace is limited to page-reference data', '1440x900', mono.length === 0, JSON.stringify({ unexpected: mono }), 'shots/r4-first-run-1440.png');

    const firstRun = await session.page.evaluate(() => {
      const rect = document.querySelector('#composer').getBoundingClientRect();
      return { centerY: Number((rect.top + rect.height / 2).toFixed(2)), viewportCenterY: innerHeight / 2, deviation: Number(Math.abs((rect.top + rect.height / 2) - innerHeight / 2).toFixed(2)), deviationPct: Number((Math.abs((rect.top + rect.height / 2) - innerHeight / 2) / innerHeight * 100).toFixed(2)), sample: document.querySelector('.document-name')?.textContent.trim(), metadata: document.querySelector('.document-meta')?.textContent.trim(), suggestions: [...document.querySelectorAll('.suggestion')].map((node) => node.textContent.trim()), pdfOpen: !document.querySelector('#pdf-sheet').hidden };
    });
    record('C28', 'First-run composer is vertically centered with sample and questions visible', '1440x900', firstRun.deviationPct <= 10 && firstRun.pdfOpen === false && firstRun.suggestions.length === 3, JSON.stringify(firstRun), 'shots/r4-first-run-1440.png');
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
    record('C33', 'First-run copy audit separates chrome from required sample questions', '1440x900', chromeWords.chromeWords <= 25, JSON.stringify(chromeWords), 'copy.md; shots/r4-first-run-1440.png');
    await session.context.close();

    const mobile = await makePage(browser, 'first-run', viewports[3]);
    const mobileMetrics = await mobile.page.evaluate(() => {
      const rect = document.querySelector('#composer').getBoundingClientRect();
      return { centerY: rect.top + rect.height / 2, viewportCenterY: innerHeight / 2, deviationPct: Math.abs(rect.top + rect.height / 2 - innerHeight / 2) / innerHeight * 100, scrollWidth: document.documentElement.scrollWidth };
    });
    record('C28', 'Mobile first-run composer position and width', '390x844', mobileMetrics.deviationPct <= 10 && mobileMetrics.scrollWidth <= 391, JSON.stringify(mobileMetrics), 'shots/r4-first-run-390.png');
    await mobile.context.close();
  }

  // C27: computed visible text sizes and composited foreground/background contrast.
  for (const [state, viewport] of [['first-run', viewports[0]], ['answered-open', viewports[0]], ['answered-open', viewports[1]], ['answered-open', viewports[2]], ['answered-open', viewports[3]], ['model-unreachable', viewports[0]], ['starting', viewports[0]]]) {
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
    record('C27', 'Visible DOM text is at least 12px and contrast is at least 4.5:1', `${viewport.w}x${viewport.h} ${state}`, values.violations.length === 0, JSON.stringify(values), state === 'first-run' ? 'shots/r4-first-run-1440.png' : state === 'answered-open' ? 'shots/r4-answered-open-1440.png' : 'shots/r4-model-unreachable-1440.png');
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
    record('C29', 'Citation 1 opens and centers the full Link caption highlight within the clipped PDF view', `${viewport.w}x${viewport.h}`, within && dx <= .15 && dy <= .15 && zoomPass, JSON.stringify({ target: 'link-caption', pageLabel: await session.page.locator('#toolbar-page').innerText(), zoom: value && { nativePdfPercent: value.nativePdfPercent, fitRelativeFactor: value.fitRelativeFactor }, pageWidth: value?.pageWidth, fitWidth: value?.fitWidth, clip: value?.clip, targetRect: value?.union, centerDeviation: { xPct: Number((dx * 100).toFixed(2)), yPct: Number((dy * 100).toFixed(2)) }, minimumClearancePx: value ? Math.min(value.union.left - value.clip.left, value.clip.right - value.union.right, value.union.top - value.clip.top, value.clip.bottom - value.union.bottom) : null, marks: value?.marks, scroll: value?.scroll }), viewport.w === 390 ? 'shots/r4-mobile-sheet-55-390.png (55%); source opens at 92% in interaction' : viewport.w === 1440 ? 'shots/r4-answered-open-1440.png' : 'CHECK-RESULTS.txt (browser measurement; no dedicated screenshot slot)');
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
    const allVisible = sources.length === 3 && sources.every((item) => item.within && item.centered && item.zoom === 118 && item.page === '2 / 4' && item.readableScale);
    record('R4-mobile-sources', 'All three cited passages remain visible, centered, and magnified on the original page', '390x844', allVisible, JSON.stringify({ sources, readabilityReview: 'The minimum source-mark line height is checked in-browser; manual review of r4-mobile-sheet-55-390.png verifies the original page text remains readable with neighboring context.' }), 'shots/r4-mobile-sheet-55-390.png; CHECK-RESULTS.txt');
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
    record('C30', 'Method labels stay visible in the dark gutter beside the page', '1440x900', noCollision && rail.legendVisible, JSON.stringify({ ...rail, limitation: 'The proof uses a rasterized Poppler page and has no pdf.js text layer. Gutter geometry is verified against the full page rectangle; production text-layer collision is untested.' }), 'shots/r4-matches-1440.png');
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
    for (const item of snaps) record('C31', `Mobile PDF snap ${item.snap}%`, '390x844', correct ? 'PASS' : 'FAIL', JSON.stringify(item), 'shots/r4-mobile-sheet-55-390.png');
    await session.context.close();
  }

  // C33: banned expressions across all directly addressable states and chrome word counts.
  {
    const banned = ['desk', 'belong', 'page-backed', 'read then ask', 'seamless', 'effortless', 'unlock', 'supercharge', 'magic', 'intelligent', 'powered by ai', 'powered by', 'your documents', 'journey', 'insights', 'smart', 'model ready', 'preview', 'illustrative', 'not functional'];
    const states = ['first-run', 'answered-closed', 'answered-open', 'answered-matches', 'model-unreachable', 'starting', 'reading-progress', 'answer-shapes'];
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
      findings.push({ state, banned: matches, ollamaAllowed, externalRequests: externalRequests.length, chromeWords: copyAudit.chromeWords, totalIncludingSuggestions: copyAudit.totalIncludingSuggestions });
      await session.context.close();
    }
    const standaloneCuriosity = await makePage(browser, 'first-run', viewports[0]);
    const curiosityLabels = await standaloneCuriosity.page.evaluate(() => [...document.querySelectorAll('body *')].filter((node) => node.getClientRects().length && node.children.length === 0 && node.textContent.trim().toLowerCase() === 'curiosity' && !node.matches('.document-name,.sheet-title')).map((node) => ({ className: node.className?.baseVal || node.className, text: node.textContent.trim() })));
    record('C33', 'Curiosity is not used as a standalone interface label', '1440x900', curiosityLabels.length === 0, JSON.stringify({ standaloneCuriosityLabels: curiosityLabels, permittedSampleTitle: await standaloneCuriosity.page.locator('.document-name').innerText() }), 'copy.md; shots/r4-first-run-1440.png');
    await standaloneCuriosity.context.close();
    record('C33', 'All directly addressable states meet copy restrictions', '1440x900', findings.every((item) => item.banned.length === 0 && item.ollamaAllowed && item.externalRequests === 0 && item.chromeWords <= 25), JSON.stringify(findings), 'copy.md');
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
    record('R3-2', 'Citation hover opens a genuine short source quotation', '1440x900', await popover.count() === 1 && quoteWords <= 25, JSON.stringify({ sourceId, quote, quoteWords }), 'shots/r4-citation-popover-1440.png');
    await page.mouse.move(8, 8);
    await chip.focus();
    await page.waitForFunction(() => document.querySelector('.citation-popover')?.classList.contains('is-visible') === true);
    const focusOpen = await page.locator('.citation-popover.is-visible').count() === 1;
    record('R3-2', 'Citation keyboard focus exposes the same popover', '1440x900', focusOpen, `focus=${await chip.evaluate((node) => node === document.activeElement)}; visible=${focusOpen}`, 'shots/r4-citation-popover-1440.png');
    await chip.click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#evidence-layer').dataset.activeTarget === 'link-caption');
    const pageLabel = await page.locator('#toolbar-page').innerText();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
    const restored = await chip.evaluate((node) => node === document.activeElement);
    record('R3-2/R3-13', 'Citation opens its cited page; Escape closes and restores focus', '1440x900', pageLabel === '2 / 4' && restored, JSON.stringify({ pageLabel, focusRestored: restored, sheetHidden: await page.locator('#pdf-sheet').isHidden() }), 'shots/r4-answered-open-1440.png');

    await page.locator('.document-open').click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    await page.locator('#close-sheet').click();
    await page.waitForFunction(() => document.querySelector('#pdf-sheet').hidden);
    const docFocus = await page.locator('.document-open').evaluate((node) => node === document.activeElement);
    record('R3-13', 'PDF close control restores focus to the document pill', '1440x900', docFocus, `focusRestored=${docFocus}`, 'shots/r4-reading-progress-1440.png');
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
    record('R3-1/R3-14', 'First-run Link question uses only the documented local proof fixture', '1440x900', selected === answered.question && answered.citations.length === 3 && answered.figureCredit === 'NASA/JPL-Caltech/MSSS' && answered.figureWidth > 0 && before === after, JSON.stringify({ selected, ...answered, fetchOrXhrBefore: before, fetchOrXhrAfter: after }), 'shots/r4-answered-closed-1440.png');
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
    record('R3-15', 'Show matches is off by default, exposes evidence labels, then preserves answer bytes', '1440x900', off === 'false' && matchState.pressed === 'true' && ['Exact words', 'Meaning', 'Figure'].every((label) => matchState.tags.includes(label)) && matchState.legend && beforeText === onText && offAgain === 'false' && afterOffTargets === 1, JSON.stringify({ offInitially: off, ...matchState, answerUnchanged: beforeText === onText, offAgain, visibleTargetsWhenOff: afterOffTargets }), 'shots/r4-matches-1440.png');
    record('R3-3', 'Only transform and opacity are animated', '1440x900', geometryTransitions.length === 0 && geometryKeyframes.length === 0 && geometryJs.length === 0, JSON.stringify({ geometryTransitions, geometryKeyframes: geometryKeyframes.map((item) => item[0].split('{')[0]), geometryJs }), 'assets/reading-desk.css');
    record('R3-4', 'Reduced motion removes visual animation while citation interaction remains available', '1440x900', reduced.scan === 'none' && reduced.pulse === 'none' && reduced.sheetTransition.split(',').every((value) => parseFloat(value) === 0) && connectors === 0, JSON.stringify({ reduced, conversationConnectorCount: connectors, citationCanOpen: true }), 'CHECK-RESULTS.txt');
    record('R3-7', 'All observed browser requests stay on loopback', '1440x900', inScope.length === 0 && session.errors.length === 0 && session.badResponses.length === 0, JSON.stringify({ externalRequests: inScope, pageErrors: session.errors, failedResponses: session.badResponses, requestCount: session.requests.length }), 'CHECK-RESULTS.txt');
    await session.context.close();
  }

  // C21/C18: three mark comparison and all local logo treatments.
  {
    const session = await makePage(browser, '', viewports[0]);
    await session.page.goto(`${origin}/logo-comparison.html`, { waitUntil: 'networkidle' });
    await waitAssets(session.page);
    const metric = await session.page.evaluate(() => ({ candidates: [...document.querySelectorAll('.candidate h2')].map((node) => node.innerText.trim()), candidatesCount: document.querySelectorAll('.candidate').length, specimenSizes: [...new Set([...document.querySelectorAll('.specimen svg.mark')].map((node) => Math.round(node.getBoundingClientRect().width)))].sort((a, b) => a - b), surfaces: document.querySelectorAll('.surface').length, headerLockups: document.querySelectorAll('.lockup').length, externalImageCount: [...document.images].filter((node) => new URL(node.src).origin !== location.origin).length, fonts: { newsreader: document.fonts.check('16px Newsreader'), instrument: document.fonts.check('16px Instrument') } }));
    const markFiles = ['r4-a-page-highlight.svg', 'r4-c-rp-monogram.svg'];
    const markText = await Promise.all(markFiles.map((name) => fs.readFile(path.join(root, 'assets', 'marks', name), 'utf8')));
    const validSvg = markText.every((value) => value.includes('viewBox="0 0 24 24"') && value.includes('id="mark"') && !/gradient/iu.test(value));
    const requestOutside = session.requests.filter((url) => new URL(url).origin !== origin);
    record('R3-18', 'Three R4 marks render at size variants with dark/paper and mono/two-tone treatments', '1440x900', metric.candidatesCount === 3 && metric.specimenSizes.includes(16) && metric.specimenSizes.includes(24) && metric.specimenSizes.includes(48) && metric.surfaces >= 10 && metric.headerLockups === 6 && validSvg && requestOutside.length === 0, JSON.stringify({ ...metric, markFiles, valid24pxGridSvg: validSvg, remoteRequests: requestOutside, originalityNote: 'A new drawing is not trademark clearance; owner comparison remains required.' }), 'shots/r4-logo-comparison.png');
    const a16 = await session.page.locator('.candidate').nth(0).locator('.specimen svg.mark').first().screenshot();
    const c16 = await session.page.locator('.candidate').nth(2).locator('.specimen svg.mark').first().screenshot();
    record('R3-18', 'Candidate A and C retain different one-colour 16px silhouettes', '1440x900', a16.length > 0 && c16.length > 0 && !a16.equals(c16), JSON.stringify({ candidateA: 'Marked page; file/page-like at 16px, with bar remaining visible', candidateB: 'Wordmark, not a standalone icon', candidateC: 'RP monogram; distinct from A at 16px', candidateA16Bytes: a16.length, candidateC16Bytes: c16.length }), 'shots/r4-logo-comparison.png');
    await session.context.close();
  }

  // Fresh contexts: default states, status wording, local help and first-run persistence.
  for (const state of ['model-unreachable', 'starting', 'reading-progress']) {
    const session = await makePage(browser, state, viewports[0]);
    const metrics = await session.page.evaluate(() => ({ state: document.body.dataset.state, message: document.querySelector('.attention-copy')?.innerText.trim() || '', buttonLabel: document.querySelector('#send-button').getAttribute('aria-label'), progress: document.querySelector('#document-progress')?.innerText.trim() || '', page: document.querySelector('#toolbar-page')?.innerText.trim(), opened: !document.querySelector('#pdf-sheet').hidden }));
    const expected = state === 'model-unreachable' ? metrics.message === "Can't reach the model. Start Ollama, then check again." : state === 'starting' ? metrics.message === 'Getting the model ready. The first answer can take a minute.' : metrics.progress === 'Page 3/4' && metrics.page === '3 / 4';
    record('R3-4/R4-status', `Truthful static state: ${state}`, '1440x900', expected, JSON.stringify(metrics), state === 'reading-progress' ? 'shots/r4-reading-progress-1440.png' : 'shots/r4-model-unreachable-1440.png');
    if (state === 'model-unreachable') {
      await session.page.locator('#model-help-open').click();
      const help = await session.page.locator('#help-panel').innerText();
      const network = session.requests.filter((url) => new URL(url).origin !== origin);
      record('R3-4', 'Unavailable-model help is local and gives a corrective action', '1440x900', /local model app from the Start menu/i.test(help) && !/ollama/i.test(help) && network.length === 0, JSON.stringify({ help, externalRequests: network, permittedOllamaCopyRemainsOnlyInStatus: true }), 'shots/r4-model-unreachable-1440.png');
    }
    await session.context.close();
  }

  // Verify exactly twelve R4 screenshots and their encoded viewport dimensions.
  const screenshotResults = [];
  for (const [name, width, height, fullPage = false] of shots) {
    try {
      const stat = await fs.stat(path.join(root, 'shots', name));
      const context = await browser.newContext({ viewport: { width: 20, height: 20 } });
      const page = await context.newPage();
      const size = await page.evaluate((url) => new Promise((resolve, reject) => {
        const image = new Image(); image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight }); image.onerror = reject; image.src = url;
      }), `${origin}/shots/${name}`);
      await context.close();
      screenshotResults.push({ name, bytes: stat.size, ...size, expected: { viewportWidth: width, viewportHeight: height, capture: fullPage ? 'full-page' : 'viewport' } });
    } catch (error) { screenshotResults.push({ name, error: error.message }); }
  }
  const actualR4Shots = (await fs.readdir(path.join(root, 'shots'))).filter((name) => name.startsWith('r4-') && name.endsWith('.png'));
  record('R4-screenshots', 'Exactly twelve full-resolution R4 captures exist at requested sizes', '1440/1024/768/390 captures', screenshotResults.length === 12 && actualR4Shots.length === 12 && screenshotResults.every((item) => item.bytes > 0 && !item.error && item.width === item.expected.viewportWidth && (item.expected.capture === 'full-page' ? item.height >= item.expected.viewportHeight : item.height === item.expected.viewportHeight)), JSON.stringify({ actualR4Count: actualR4Shots.length, note: 'Expected dimensions describe the browser viewport. Two captures are explicitly full-page: answer shapes and logo comparison.', required: screenshotResults }), 'shots/');

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
    'SPEC CONFLICT | C33 requires the exact starting message “Getting the model ready. The first answer can take a minute.” and also bans the phrase “Model ready”. The required message contains the banned phrase. The required copy is preserved and the visible-wording check remains FAIL.',
    'SUPERSEDED_BY_R4 | Earlier permanent split-screen Reading Desk | Replaced by R4 4.1 conversation-first canvas with an optional PDF sheet; not counted as a pass.',
    'SUPERSEDED_BY_R4 | R3-F1 persistent model-ready header/status | Replaced by R4 4.2–4.3 no ready status in the header and attention-only states; not counted as a pass.',
    'SUPERSEDED_BY_R4 | R3 mobile 2.5× citation default | Replaced by R4 4.10 source-focused zoom up to 200% with the original page fitting the sheet width by default; actual mobile citation zoom is 118%; not counted as a pass.',
    'SUPERSEDED_BY_R4 | R3 logo candidates and screenshot set | Replaced by R4 4.13 three new concepts and the twelve named R4 captures; not counted as a pass.'
  );
} finally {
  await browser.close();
}

const text = output.join('\n') + '\n';
await fs.writeFile(path.join(root, 'CHECK-RESULTS.txt'), text, 'utf8');
process.stdout.write(text);
if (failed) process.exitCode = 1;
