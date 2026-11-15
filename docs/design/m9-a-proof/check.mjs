import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';

const require = createRequire(path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@playwright', 'cli', 'package.json'));
const { chromium } = require('playwright');
const root = path.resolve('docs/design/m9-a-proof');
const origin = 'http://127.0.0.1:8765';
const entry = `${origin}/proof-static-not-functional.html`;
const viewports = [{ w: 1440, h: 900 }, { w: 1024, h: 768 }, { w: 390, h: 844 }];
const states = ['first-run', 'answered-closed', 'answered-open', 'answered-matches', 'model-missing', 'reading-progress'];
const lines = [
  'M9-A-R3 Reading Desk static proof checks',
  'Browser: Playwright-managed Chrome channel; exact version recorded below.',
  'Classification: These checks establish proof rendering and scripted local interactions only. No product API, retrieval, generation, ingestion, or production PDF.js behavior was exercised.',
  'CLS values exclude shifts after recent user input, as defined for the browser CLS metric; raw observed values are also reported for PDF opening.',
  '',
];
let failures = 0;
let passed = 0;
const record = (criterion, name, ok, detail, status = ok ? 'PASS' : 'FAIL') => {
  lines.push(`${status} | ${criterion} | ${name} | ${detail}`);
  if (status === 'PASS') passed += 1;
  if (status === 'FAIL') failures += 1;
};
const wordCount = (value) => (value.trim().match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*(?:-[\p{L}\p{N}]+)*/gu) || []).length;
const ratios = (foreground, background) => {
  const channel = (value) => { const v = value / 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const luminance = (hex) => {
    const value = hex.replace('#', '');
    return 0.2126 * channel(parseInt(value.slice(0, 2), 16)) + 0.7152 * channel(parseInt(value.slice(2, 4), 16)) + 0.0722 * channel(parseInt(value.slice(4, 6), 16));
  };
  const a = luminance(foreground), b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};
const waitForAssets = (page) => page.evaluate(async () => {
  await document.fonts.ready;
  await Promise.all([...document.images].map((image) => image.decode().catch(() => undefined)));
});
const makePage = async (browser, url, viewport, { reducedMotion = 'no-preference' } = {}) => {
  const context = await browser.newContext({ viewport: { width: viewport.w, height: viewport.h }, deviceScaleFactor: 1, reducedMotion });
  const page = await context.newPage();
  const external = [], failedResponses = [], errors = [];
  page.on('request', request => { try { if (new URL(request.url()).origin !== origin) external.push(request.url()); } catch { external.push(request.url()); } });
  page.on('response', response => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__m9Cls = 0;
    window.__m9RawShift = 0;
    window.__m9ShiftCount = 0;
    if (PerformanceObserver.supportedEntryTypes.includes('layout-shift')) {
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__m9RawShift += entry.value;
          if (!entry.hadRecentInput) window.__m9Cls += entry.value;
          window.__m9ShiftCount += 1;
        }
      }).observe({ type: 'layout-shift', buffered: true });
    }
  });
  await page.goto(url, { waitUntil: 'networkidle' });
  await waitForAssets(page);
  return { context, page, external, failedResponses, errors };
};
const visibleClipAndMarks = async (page, sourceId) => page.evaluate((id) => {
  const intersect = (a, b) => ({ left: Math.max(a.left, b.left), top: Math.max(a.top, b.top), right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom) });
  const viewport = { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
  const sheet = document.querySelector('#pdf-sheet');
  const pageViewport = document.querySelector('#page-viewport');
  if (!sheet || sheet.hidden || !pageViewport) return null;
  let clip = intersect(viewport, sheet.getBoundingClientRect());
  clip = intersect(clip, pageViewport.getBoundingClientRect());
  for (let node = pageViewport.parentElement; node; node = node.parentElement) {
    const style = getComputedStyle(node), rect = node.getBoundingClientRect();
    if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX)) { clip.left = Math.max(clip.left, rect.left); clip.right = Math.min(clip.right, rect.right); }
    if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY)) { clip.top = Math.max(clip.top, rect.top); clip.bottom = Math.min(clip.bottom, rect.bottom); }
  }
  const target = document.querySelector(`.evidence-target[data-evidence-target="${id}"]`);
  const marks = [...(target?.querySelectorAll('.evidence-mark') || [])].map(mark => { const r = mark.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; });
  const union = marks.length ? { top: Math.min(...marks.map(r => r.top)), bottom: Math.max(...marks.map(r => r.bottom)) } : null;
  const pageFrame = document.querySelector('#page-frame');
  const image = document.querySelector('#page-image');
  return {
    clip, marks, union,
    scrollTop: pageViewport.scrollTop, scrollLeft: pageViewport.scrollLeft,
    scrollHeight: pageViewport.scrollHeight, scrollWidth: pageViewport.scrollWidth,
    clientHeight: pageViewport.clientHeight, clientWidth: pageViewport.clientWidth,
    frame: pageFrame ? { width: pageFrame.getBoundingClientRect().width, height: pageFrame.getBoundingClientRect().height } : null,
    image: image ? { naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight, width: image.getBoundingClientRect().width, height: image.getBoundingClientRect().height } : null,
  };
}, sourceId);

const answerGlyphsIntersectingComposer = async (page) => page.evaluate(() => {
  const composer = document.querySelector('#composer')?.getBoundingClientRect();
  const appNode = document.querySelector('#app');
  if (!composer || !appNode) return [];
  const appRect = appNode.getBoundingClientRect();
  const appClip = { left: appRect.left + appNode.clientLeft, top: appRect.top + appNode.clientTop, right: appRect.left + appNode.clientLeft + appNode.clientWidth, bottom: appRect.top + appNode.clientTop + appNode.clientHeight };
  const hits = [];
  for (const paragraph of document.querySelectorAll('.answer-paragraph')) {
    const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const textNode = walker.currentNode;
      for (let index = 0; index < textNode.length; index += 1) {
        const range = document.createRange();
        range.setStart(textNode, index);
        range.setEnd(textNode, index + 1);
        for (const rect of range.getClientRects()) {
          if (rect.width > 0 && rect.height > 0 && rect.right > composer.left && rect.left < composer.right && rect.bottom > composer.top && rect.top < composer.bottom && rect.right > appClip.left && rect.left < appClip.right && rect.bottom > appClip.top && rect.top < appClip.bottom) {
            hits.push({ text: textNode.data[index], rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }, composerTop: composer.top });
          }
        }
      }
    }
  }
  return hits;
});

const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  lines[1] = `Browser: Playwright-managed Chrome channel ${browser.version()} (headless).`;
  const stateResults = new Map();
  for (const state of states) {
    for (const viewport of viewports) {
      const label = `${state} ${viewport.w}x${viewport.h}`;
      let session;
      try {
        session = await makePage(browser, `${entry}?state=${state}`, viewport);
        const { page, external, failedResponses, errors } = session;
        const metrics = await page.evaluate(() => {
          const rect = (selector) => { const node = document.querySelector(selector); if (!node || node.hidden) return null; const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; };
          const visibleText = () => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const pieces = [];
            while (walker.nextNode()) {
              const node = walker.currentNode, parent = node.parentElement;
              if (!parent || parent.closest('.skip-link,.assistant-message,.user-message,.figure-caption,.popover-quote,[hidden]')) continue;
              const style = getComputedStyle(parent);
              if (style.display === 'none' || style.visibility === 'hidden' || !parent.getClientRects().length) continue;
              const value = node.nodeValue.trim(); if (value) pieces.push(value);
            }
            return pieces.join(' ');
          };
          const thread = rect('#thread'), sheet = rect('#pdf-sheet'), composer = rect('#composer');
          const paragraphStyles = [...document.querySelectorAll('.assistant-message,.answer-paragraph')].map(el => { const s = getComputedStyle(el); return { selector: el.className, color: s.color, background: s.backgroundColor, border: [s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth].join('/'), fontSize: s.fontSize, lineHeight: s.lineHeight }; });
          const answerLineLengths = [...document.querySelectorAll('.answer-paragraph')].map(paragraph => {
            const node = [...paragraph.childNodes].find(child => child.nodeType === Node.TEXT_NODE);
            if (!node) return [];
            const lineCounts = new Map();
            for (let index = 0; index < node.textContent.length; index += 1) {
              const range = document.createRange();
              range.setStart(node, index);
              range.setEnd(node, index + 1);
              const lineTop = Math.round(range.getBoundingClientRect().top * 2) / 2;
              lineCounts.set(lineTop, (lineCounts.get(lineTop) || 0) + 1);
            }
            return [...lineCounts.values()];
          });
          const buttonTargets = [...document.querySelectorAll('button')].filter(el => el.getClientRects().length && !el.closest('[hidden]')).map(el => { const r=el.getBoundingClientRect(); return {label:el.getAttribute('aria-label')||el.title||el.innerText.trim(),width:r.width,height:r.height}; });
          const fonts = { reading: document.fonts.check('18px "Newsreader"'), interface: document.fonts.check('16px "Instrument Sans"'), mono: document.fonts.check('12px "IBM Plex Mono"') };
          const text = visibleText();
          return {
            state: document.body.dataset.state, text, wordCount: (text.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*(?:-[\p{L}\p{N}]+)*/gu)||[]).length,
            width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight,
            cls: window.__m9Cls, rawShift: window.__m9RawShift, shiftCount: window.__m9ShiftCount,
            thread, sheet, composer, threadCentered: thread ? Math.abs((thread.x+thread.right)/2-innerWidth/2) : null,
            sheetHidden: document.querySelector('#pdf-sheet').hidden, page: document.querySelector('#page-count')?.textContent.trim(),
            status: document.querySelector('#status-label')?.textContent.trim(), answerCount: document.querySelectorAll('.answer-paragraph').length,
            citations: document.querySelectorAll('.citation-chip').length, figureLoaded: [...document.images].some(img=>img.classList.contains('figure-image')&&img.complete&&img.naturalWidth>0) || (document.querySelector('.inline-figure img')?.complete&&document.querySelector('.inline-figure img')?.naturalWidth>0),
            sampleTitle: document.querySelector('.sample-context')?.textContent.trim(), paragraphStyles, buttonTargets, fonts,
            answerLineLengths,
            pageImage: document.querySelector('#page-image')?.getAttribute('src'), matchState: document.querySelector('#matches-toggle')?.getAttribute('aria-pressed'),
            visibleTargets: [...document.querySelectorAll('.evidence-target')].filter(el=>getComputedStyle(el).visibility!=='hidden'&&Number(getComputedStyle(el).opacity)>0).map(el=>el.dataset.evidenceTarget),
            methodLabels: [...document.querySelectorAll('.method-tag')].filter(el=>getComputedStyle(el).display!=='none').map(el=>el.textContent.trim()),
            proseFonts: [...document.querySelectorAll('.assistant-message')].map(el=>getComputedStyle(el).fontFamily),
          };
        });
        stateResults.set(`${state}:${viewport.w}`, { ...metrics, external, failedResponses, errors });
        record('C5/C7', `${label}: local assets and no page errors`, external.length === 0 && failedResponses.length === 0 && errors.length === 0, `external=${external.length}; failedResponses=${failedResponses.length}; pageErrors=${errors.length}`);
        record('C5', `${label}: no horizontal overflow`, metrics.scrollWidth <= viewport.w + 1, `scrollWidth=${metrics.scrollWidth}px, viewport=${viewport.w}px`);
        record('C11', `${label}: visible interface copy limit`, metrics.wordCount <= 25, `${metrics.wordCount} visible interface words; answer, user query, document caption and credit excluded`);
        const normalizedText=metrics.text.toLowerCase().normalize('NFKC').replace(/[^\p{L}\p{N}]+/gu,' ').trim().replace(/\s+/gu,' ');
        const bannedExpressions=['preview','illustrative','not functional','desk','belong','page backed','read then ask','seamless','effortless','unlock','supercharge','magic','intelligent','powered by ai','your documents','journey','insights'];
        const paddedText=` ${normalizedText} `;
        const matchedBanned=bannedExpressions.filter(term=>paddedText.includes(` ${term} `));
        record('C9', `${label}: restricted visible wording absent`, matchedBanned.length===0, `matches=${JSON.stringify(matchedBanned)}; normalized visible text=${JSON.stringify(normalizedText)}`);
        record('C7/C17', `${label}: fonts used by visible content are bundled and loaded`, metrics.fonts.interface && metrics.fonts.mono && (metrics.answerCount===0 || metrics.fonts.reading), JSON.stringify({...metrics.fonts,readingFontRequired:metrics.answerCount>0}));
        const composerEnabled = !(await page.locator('#question-input').isDisabled());
        const attachEnabled = !(await page.locator('#attach-button').isDisabled());
        record('C14', `${label}: optional attachment and composer available`, composerEnabled && attachEnabled, `composerEnabled=${composerEnabled}; attachEnabled=${attachEnabled}`);
        const targetFailures = metrics.buttonTargets.filter(target => target.width < 44 || target.height < 44);
        record('C2/C12', `${label}: visible buttons meet 44px target`, targetFailures.length === 0, targetFailures.length ? JSON.stringify(targetFailures) : `${metrics.buttonTargets.length} visible buttons; minimum measured target 44px`);
        if (state === 'first-run') {
          record('C14', `${label}: sample, three prompts and closed reader`, metrics.sampleTitle === 'Curiosity' && await page.locator('.suggestion').count() === 3 && metrics.sheetHidden, `sample=${metrics.sampleTitle}; prompts=${await page.locator('.suggestion').count()}; sheetHidden=${metrics.sheetHidden}`);
          record('C13', `${label}: fresh default conversation remains centered`, metrics.sheetHidden && Math.abs(metrics.threadCentered) < 1, `sheetHidden=${metrics.sheetHidden}; centerDelta=${metrics.threadCentered?.toFixed(2)}px`);
        }
        if (state === 'answered-closed' || state === 'answered-open' || state === 'answered-matches') {
          record('C1', `${label}: answer, inline citations and genuine figure`, metrics.answerCount === 3 && metrics.citations === 3 && metrics.figureLoaded, `answerParagraphs=${metrics.answerCount}; citations=${metrics.citations}; Link figure loaded=${metrics.figureLoaded}`);
          record('C13', `${label}: viewer state matches URL state`, (state === 'answered-closed') === metrics.sheetHidden, `sheetHidden=${metrics.sheetHidden}; page=${metrics.page}`);
          record('C17', `${label}: answer reading type and size`, metrics.proseFonts.every(font => font.includes('Newsreader')) && metrics.paragraphStyles.every(item=>parseFloat(item.fontSize)>=17 && parseFloat(item.lineHeight)/parseFloat(item.fontSize)>=1.6), JSON.stringify(metrics.paragraphStyles));
          if (state === 'answered-closed' && viewport.w === 1440) {
            const lengths = metrics.answerLineLengths.flat();
            const average = lengths.length ? lengths.reduce((sum, length) => sum + length, 0) / lengths.length : 0;
            record('C17', `${label}: actual answer line length at the default conversation width`, average >= 60 && average <= 75, `visual text-node line lengths=${JSON.stringify(metrics.answerLineLengths)}; average=${average.toFixed(1)} characters per line; citations excluded`);
          }
          if (state === 'answered-closed' && viewport.w === 390) {
            const lengths = metrics.answerLineLengths.flat();
            const average = lengths.length ? lengths.reduce((sum, length) => sum + length, 0) / lengths.length : 0;
            record('C17', `${label}: narrow-screen answer line length`, lengths.length > 0 && average >= 25 && average <= 55, `visual text-node line lengths=${JSON.stringify(metrics.answerLineLengths)}; average=${average.toFixed(1)} characters per line; citations excluded; readable type=${metrics.paragraphStyles.every(item=>parseFloat(item.fontSize)>=17)}`);
          }
        }
        if (state === 'answered-open' || state === 'answered-matches' || state === 'reading-progress') {
          const expectedPage = state === 'reading-progress' ? '3 / 4' : '2 / 4';
          record('C1/C13', `${label}: PDF page and sheet opened`, !metrics.sheetHidden && metrics.page === expectedPage, `sheetHidden=${metrics.sheetHidden}; page=${metrics.page}; sheet=${JSON.stringify(metrics.sheet)}`);
          if (viewport.w > 760) record('C13', `${label}: desktop PDF sheet occupies approximately 55%`, metrics.sheet && Math.abs(metrics.sheet.width / viewport.w - .55) < .035, `sheetWidth=${metrics.sheet?.width}px (${(metrics.sheet?.width/viewport.w*100).toFixed(1)}% viewport)`);
          if (viewport.w <= 760) record('C13', `${label}: mobile PDF opens as a bottom sheet`, metrics.sheet && metrics.sheet.y > 0 && metrics.sheet.bottom <= viewport.h + 1 && metrics.composer && metrics.composer.bottom <= metrics.sheet.y + 1, `sheetTop=${metrics.sheet?.y}px; sheetBottom=${metrics.sheet?.bottom}px; composerBottom=${metrics.composer?.bottom}px`);
        }
        if (state === 'answered-matches') {
          record('C15', `${label}: Show matches on exposes on-page sources`, metrics.matchState === 'true' && metrics.visibleTargets.length === 4 && ['words','meaning','figure'].every(method=>metrics.methodLabels.includes(method)), `pressed=${metrics.matchState}; targets=${metrics.visibleTargets.join(',')}; methods=${metrics.methodLabels.join(',')}`);
        }
        if (state === 'model-missing') {
          record('F1-F', `${label}: unavailable-model line names the local corrective action`, metrics.status === 'Ollama unavailable — start Ollama.' && metrics.answerCount === 0 && metrics.text.includes('Ollama unavailable') && metrics.text.includes('start Ollama'), `status=${JSON.stringify(metrics.status)}; answerParagraphs=${metrics.answerCount}; message=${metrics.text.includes('Ollama unavailable') && metrics.text.includes('start Ollama')}`);
        }
        if (state === 'reading-progress') record('C4', `${label}: reading page and progress strip visible`, metrics.pageImage === 'assets/pages/page-03.png' && !metrics.sheetHidden, `page image=${metrics.pageImage}; page=${metrics.page}`);
        await session.context.close();
      } catch (error) {
        record('C1-C19', `${label}: state renders and checks execute`, false, error.stack || error.message);
        if (session) await session.context.close().catch(() => undefined);
      }
    }
  }

  // Corrective gate: actual answer glyphs may never sit underneath the fixed composer.
  {
    const session = await makePage(browser, `${entry}?state=answered-closed`, { w: 1024, h: 768 });
    const { page } = session;
    const initialHits = await answerGlyphsIntersectingComposer(page);
    const initialScroll = await page.evaluate(() => ({ appTop: document.querySelector('#app').scrollTop, appMax: document.querySelector('#app').scrollHeight - document.querySelector('#app').clientHeight }));
    record('F1-A', '1024px answer text does not intersect the fixed composer on arrival', initialHits.length === 0, `overlappingGlyphs=${initialHits.length}; first=${JSON.stringify(initialHits[0] || null)}; appScroll=${JSON.stringify(initialScroll)}`);
    await page.evaluate(() => { const app = document.querySelector('#app'); app.scrollTop = app.scrollHeight; });
    await page.waitForTimeout(50);
    const finalLine = await page.evaluate(() => {
      const paragraph = document.querySelectorAll('.answer-paragraph')[2];
      const composer = document.querySelector('#composer').getBoundingClientRect();
      const textNode = [...paragraph.childNodes].find(node => node.nodeType === Node.TEXT_NODE);
      const range = document.createRange();
      range.selectNodeContents(textNode);
      const rects = [...range.getClientRects()].filter(rect => rect.width && rect.height);
      const last = rects.at(-1);
      return { text: textNode.textContent.trim(), top: last?.top, bottom: last?.bottom, composerTop: composer.top, appScrollTop: document.querySelector('#app').scrollTop, appMax: document.querySelector('#app').scrollHeight - document.querySelector('#app').clientHeight };
    });
    record('F1-A', '1024px final answer sentence is reachable above the composer by scrolling the conversation', !!finalLine.bottom && finalLine.bottom <= finalLine.composerTop - 8 && finalLine.appMax > 0, JSON.stringify(finalLine));
    await session.context.close();
  }
  for (const viewport of [{ w: 390, h: 844 }, { w: 1440, h: 900 }]) {
    const session = await makePage(browser, `${entry}?state=answered-closed`, viewport);
    const hits = await answerGlyphsIntersectingComposer(session.page);
    record('F1-A', `${viewport.w}px answer glyphs stay outside the visible composer region`, hits.length === 0, `visibleGlyphIntersections=${hits.length}; example=${JSON.stringify(hits[0] || null)}`);
    await session.context.close();
  }

  // Corrective gate: the first sample prompt has one deterministic, zero-network proof path.
  {
    const session = await makePage(browser, `${entry}?state=first-run`, { w: 1440, h: 900 });
    const { page, external } = session;
    const requestCount = await page.evaluate(() => performance.getEntriesByType('resource').filter(entry => ['fetch','xmlhttprequest'].includes(entry.initiatorType)).length);
    await page.locator('.suggestion').first().click();
    const selected = await page.locator('#question-input').inputValue();
    await page.locator('#send-button').click();
    const answered = await page.locator('.assistant-message').count();
    await page.waitForFunction(() => { const image=document.querySelector('.inline-figure img'); return image?.complete && image.naturalWidth > 0; });
    const visibleQuestion = await page.locator('.user-message').innerText().catch(() => '');
    const citations = await page.locator('.citation-chip').count();
    const figureCredit = await page.locator('.figure-credit').innerText().catch(() => '');
    const figureLoaded = await page.locator('.inline-figure img').evaluate(image => image.complete && image.naturalWidth > 0);
    const afterRequests = await page.evaluate(() => performance.getEntriesByType('resource').filter(entry => ['fetch','xmlhttprequest'].includes(entry.initiatorType)).length);
    record('F1-C', 'Link suggestion sends the matching pre-authored answer, loaded figure and citations with no backend request', selected === 'What does Link show?' && answered === 1 && visibleQuestion === selected && citations === 3 && figureCredit === 'NASA/JPL-Caltech/MSSS' && figureLoaded && requestCount === afterRequests && external.length === 0, `selected=${JSON.stringify(selected)}; answerCount=${answered}; displayedQuestion=${JSON.stringify(visibleQuestion)}; citations=${citations}; figureLoaded=${figureLoaded}; credit=${JSON.stringify(figureCredit)}; requests=${requestCount}->${afterRequests}; external=${external.length}`);
    await session.context.close();
  }

  // Corrective gate: only the bundled NASA sample may enter the scripted reading state.
  {
    const session = await makePage(browser, `${entry}?state=first-run`, { w: 1440, h: 900 });
    const { page, external } = session;
    await page.locator('#attach-button').click();
    await page.locator('#pdf-input').setInputFiles(path.resolve('data/pdf/mars-science-laboratory.pdf'));
    const selectedName = await page.locator('#attachment-name').innerText();
    const openAction = page.getByRole('button', { name: 'Open sample pages' });
    await openAction.waitFor({ state: 'visible', timeout: 5000 }).catch(() => undefined);
    const actionVisible = await openAction.isVisible().catch(() => false);
    if (actionVisible) await openAction.click();
    const reading = await page.evaluate(() => ({ state: document.body.dataset.state, title: document.querySelector('#sheet-title').textContent, page: document.querySelector('#page-count').textContent.trim(), open: !document.querySelector('#pdf-sheet').hidden, filename: document.querySelector('#attachment-name').textContent.trim() }));
    record('F1-D', 'selecting the checked-in NASA sample offers an explicit local reading action', selectedName === 'mars-science-laboratory.pdf' && actionVisible && reading.open && reading.page === '3 / 4' && reading.filename === selectedName && external.length === 0, `selected=${selectedName}; actionVisible=${actionVisible}; reading=${JSON.stringify(reading)}; external=${external.length}`);
    await session.context.close();
  }

  // Three-phase CLS measurement from a fresh first-run context.
  {
    const session = await makePage(browser, `${entry}?state=first-run`, { w: 1440, h: 900 });
    const page = session.page;
    await page.waitForTimeout(100);
    const initial = await page.evaluate(() => ({ cls: window.__m9Cls, raw: window.__m9RawShift, entries: window.__m9ShiftCount }));
    await page.evaluate(() => { window.__m9Cls=0; window.__m9RawShift=0; window.__m9ShiftCount=0; window.__setProofState('answered-closed'); });
    await page.waitForTimeout(300);
    const answer = await page.evaluate(() => ({ cls: window.__m9Cls, raw: window.__m9RawShift, entries: window.__m9ShiftCount }));
    await page.locator('.citation-chip').first().click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden && document.querySelector('#pdf-sheet').classList.contains('is-open'));
    await page.waitForTimeout(280);
    const opening = await page.evaluate(() => ({ cls: window.__m9Cls, raw: window.__m9RawShift, entries: window.__m9ShiftCount }));
    record('C5', '1440x900 initial-load CLS', initial.cls === 0, `CLS=${initial.cls}; raw=${initial.raw}; entries=${initial.entries}`);
    record('C5', '1440x900 answer-state-change CLS', answer.cls === 0, `CLS=${answer.cls}; raw=${answer.raw}; entries=${answer.entries}`);
    record('C5', '1440x900 citation-open CLS', opening.cls === 0, `CLS=${opening.cls}; raw=${opening.raw}; entries=${opening.entries}; click is a recent user input`);
    await session.context.close();
  }

  // Citation hover/focus/click, correct target, Escape and close-button focus restoration.
  for (const sourceId of ['link-caption', 'stream-flow', 'john-klein-water']) {
    const session = await makePage(browser, `${entry}?state=answered-closed`, { w: 1440, h: 900 });
    const { page } = session;
    const chip = page.locator(`.citation-chip[data-source-id="${sourceId}"]`);
    await chip.hover();
    const hoverShown = await page.locator(`#citation-popover-${sourceId}`).isVisible();
    const quoteWords = await page.locator(`#citation-popover-${sourceId} .popover-quote`).innerText().then(wordCount);
    record('C2', `${sourceId}: hover opens short source quotation`, hoverShown && quoteWords <= 25, `popoverVisible=${hoverShown}; quoteWords=${quoteWords}`);
    await chip.focus();
    const focusShown = await page.locator(`#citation-popover-${sourceId}`).isVisible();
    record('C2', `${sourceId}: keyboard focus opens source quotation`, focusShown, `popoverVisible=${focusShown}; aria-expanded=${await chip.getAttribute('aria-expanded')}`);
    await chip.click();
    await page.waitForFunction((id) => document.querySelector('#evidence-layer').dataset.activeTarget === id && !document.querySelector('#pdf-sheet').hidden, sourceId);
    const target = await page.evaluate((id) => {
      const node=document.querySelector(`.evidence-target[data-evidence-target="${id}"]`);
      const source=window.__sources.find(item=>item.id===id);
      return { active:document.querySelector('#evidence-layer').dataset.activeTarget, visible:getComputedStyle(node).visibility, page:document.querySelector('#page-count').textContent.trim(), markCount:node.querySelectorAll('.evidence-mark').length, expected:source.boxes.length, sourcePage:source.page };
    }, sourceId);
    record('C1/C2/C13', `${sourceId}: chip opens its exact source highlight`, target.active === sourceId && target.visible === 'visible' && target.page === '2 / 4' && target.markCount === target.expected, JSON.stringify(target));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(250);
    record('C13', `${sourceId}: Escape closes viewer and restores citation focus`, await page.locator('#pdf-sheet').isHidden() && await chip.evaluate(el=>el===document.activeElement), `sheetHidden=${await page.locator('#pdf-sheet').isHidden()}; focusRestored=${await chip.evaluate(el=>el===document.activeElement)}`);
    await chip.click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    await page.locator('#close-sheet').click();
    await page.waitForTimeout(250);
    record('C13', `${sourceId}: close button closes viewer and restores citation focus`, await page.locator('#pdf-sheet').isHidden() && await chip.evaluate(el=>el===document.activeElement), `sheetHidden=${await page.locator('#pdf-sheet').isHidden()}; focusRestored=${await chip.evaluate(el=>el===document.activeElement)}`);
    await session.context.close();
  }

  // The unavailable-model screen still exposes an explicit reading action without inventing a retrieved match.
  {
    const session = await makePage(browser, `${entry}?state=model-missing`, { w: 390, h: 844 });
    const { page } = session;
    const action = page.locator('.open-sample');
    const targetBox = await action.boundingBox();
    record('C13/C14', 'model unavailable retains a usable optional PDF action', await action.isVisible() && targetBox.width>=44 && targetBox.height>=44, `visible=${await action.isVisible()}; target=${JSON.stringify(targetBox)}`);
    await action.click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    const evidence = await page.evaluate(() => ({page:document.querySelector('#page-count').textContent.trim(),active:document.querySelector('#evidence-layer').dataset.activeTarget,visibleMarks:[...document.querySelectorAll('.evidence-target')].filter(node=>getComputedStyle(node).visibility!=='hidden').length,matchesHidden:document.querySelector('#matches-toggle').hidden,answerCount:document.querySelectorAll('.answer-paragraph').length}));
    record('C1/C13', 'model-missing PDF opens without a fabricated answer or retrieval mark', evidence.page==='2 / 4' && evidence.active==='' && evidence.visibleMarks===0 && evidence.matchesHidden && evidence.answerCount===0, JSON.stringify(evidence));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(240);
    record('C13', 'model-missing PDF close returns keyboard focus to Open PDF', await page.locator('#pdf-sheet').isHidden() && await action.evaluate(el=>el===document.activeElement), `sheetHidden=${await page.locator('#pdf-sheet').isHidden()}; focusRestored=${await action.evaluate(el=>el===document.activeElement)}`);
    await session.context.close();
  }

  // All three mobile citation targets must fit in the effective clipping intersection.
  for (const sourceId of ['link-caption', 'stream-flow', 'john-klein-water']) {
    const session = await makePage(browser, `${entry}?state=answered-closed`, { w: 390, h: 844 });
    const { page } = session;
    await page.locator(`.citation-chip[data-source-id="${sourceId}"]`).click();
    await page.waitForFunction((id) => document.querySelector('#evidence-layer').dataset.activeTarget === id && !document.querySelector('#pdf-sheet').hidden, sourceId);
    await page.waitForTimeout(220);
    const value = await visibleClipAndMarks(page, sourceId);
    const contained = !!value && value.marks.length > 0 && value.marks.every(mark => mark.left >= value.clip.left - 1 && mark.right <= value.clip.right + 1 && mark.top >= value.clip.top - 1 && mark.bottom <= value.clip.bottom + 1);
    const topClearance = value ? value.union.top - value.clip.top : -1;
    const bottomClearance = value ? value.clip.bottom - value.union.bottom : -1;
    const zoom = value?.image ? value.image.width / Math.min(520, value.clientWidth - 24) : 0;
    const pageAspect = value?.image ? value.image.width / value.image.height : 0;
    const markHeight = value?.marks.length ? Math.min(...value.marks.map(mark => mark.bottom - mark.top)) : 0;
    record('F1-B', `${sourceId}: mobile passage is magnified, proportionate and fully visible`, contained && topClearance >= 12 && bottomClearance >= 20 && zoom >= 2.45 && Math.abs(pageAspect - 1105 / 1430) < .005 && markHeight >= 14.5 && value.scrollLeft > 0 && value.scrollHeight > value.clientHeight, `marks=${value?.marks.length}; allMarksInsideClip=${contained}; clip=${JSON.stringify(value?.clip)}; topClearance=${topClearance.toFixed(2)}px; bottomClearance=${bottomClearance.toFixed(2)}px; zoom=${zoom.toFixed(2)}x; aspect=${pageAspect.toFixed(4)}; minHighlightLine=${markHeight.toFixed(2)}px; pageScroll=${value?.scrollLeft},${value?.scrollTop}; pageExtent=${value?.scrollWidth}x${value?.scrollHeight}; viewport=${value?.clientWidth}x${value?.clientHeight}`);
    await page.locator('#zoom-fit').click();
    const fit = await page.evaluate(() => ({ width: document.querySelector('#page-image').getBoundingClientRect().width, height: document.querySelector('#page-image').getBoundingClientRect().height, naturalWidth: document.querySelector('#page-image').naturalWidth, naturalHeight: document.querySelector('#page-image').naturalHeight, zoomed: document.querySelector('#page-viewport').classList.contains('is-zoomed') }));
    record('F1-B', `${sourceId}: Fit page returns the original page to full-page scale`, !fit.zoomed && Math.abs(fit.width / fit.height - 1105 / 1430) < .005 && fit.width <= 390, JSON.stringify(fit));
    await session.context.close();
  }

  // Mobile popover viewport clamping and page-open preference behavior.
  {
    const session = await makePage(browser, `${entry}?state=answered-closed`, { w: 390, h: 844 });
    const { page } = session;
    const chip = page.locator('.citation-chip').last();
    await chip.focus();
    const popRect = await page.locator('.citation-popover').last().boundingBox();
    record('C2', 'mobile citation popover remains within viewport', !!popRect && popRect.x >= 12 && popRect.x + popRect.width <= 390 && popRect.y >= 12 && popRect.y + popRect.height <= 844, JSON.stringify(popRect));
    await chip.click();
    await page.waitForFunction(() => !document.querySelector('#pdf-sheet').hidden);
    await page.reload({ waitUntil: 'networkidle' });
    await waitForAssets(page);
    record('C13', 'explicit open preference is remembered per document', !(await page.locator('#pdf-sheet').isHidden()), `sheetHidden=${await page.locator('#pdf-sheet').isHidden()}`);
    await page.locator('#close-sheet').click();
    await page.waitForTimeout(240);
    await page.reload({ waitUntil: 'networkidle' });
    record('C13', 'explicit closed preference is remembered per document', await page.locator('#pdf-sheet').isHidden(), `sheetHidden=${await page.locator('#pdf-sheet').isHidden()}`);
    await session.context.close();
  }

  // Show matches toggles only page overlays, not the answer.
  {
    const session = await makePage(browser, `${entry}?state=answered-open`, { w: 1440, h: 900 });
    const { page } = session;
    const beforeText = await page.locator('.assistant-message').innerText();
    const initiallyOff = await page.locator('#matches-toggle').getAttribute('aria-pressed') === 'false';
    await page.locator('#matches-toggle').click();
    const afterText = await page.locator('.assistant-message').innerText();
    const visibleOn = await page.locator('.evidence-target:visible').count();
    const methodNames = await page.locator('.method-tag:visible').allInnerTexts();
    await page.locator('#matches-toggle').click();
    const visibleOff = await page.locator('.evidence-target:visible').count();
    record('C15', 'matches default off; toggle shows representative page overlays', initiallyOff && visibleOn === 4 && ['words','meaning','figure'].every(name=>methodNames.includes(name)), `initiallyOff=${initiallyOff}; visibleTargetsOn=${visibleOn}; methods=${methodNames.join(',')}`);
    record('C15', 'turning matches off removes extra marks; answer is unchanged', visibleOff === 1 && beforeText === afterText, `visibleTargetsOff=${visibleOff}; answerByteEquivalent=${beforeText===afterText}`);
    await session.context.close();
  }

  // Reduced motion disables the three defined motions, while citation opening still works.
  {
    const session = await makePage(browser, `${entry}?state=answered-closed`, { w: 390, h: 844 }, { reducedMotion: 'reduce' });
    const { page } = session;
    const motion = await page.evaluate(() => ({
      scan: getComputedStyle(document.querySelector('.page-thumb[aria-current="page"]'),'::after').animationName,
      pulse: getComputedStyle(document.querySelector('.evidence-mark')).animationName,
      sheetTransition: getComputedStyle(document.querySelector('#pdf-sheet')).transitionDuration,
    }));
    await page.locator('.citation-chip').first().click();
    const opened = !(await page.locator('#pdf-sheet').isHidden());
    record('C4', 'reduced motion removes scan, pulse and sheet transition', motion.scan === 'none' && motion.pulse === 'none' && motion.sheetTransition.split(',').every(value=>parseFloat(value)===0), JSON.stringify(motion));
    record('C4', 'reduced motion preserves citation opening', opened, `opened=${opened}; selected=${await page.locator('#evidence-layer').getAttribute('data-active-target')}`);
    await session.context.close();
  }

  // Attachment selection is optional; the bundled sample has a scripted reading path, arbitrary PDFs do not.
  {
    const session = await makePage(browser, `${entry}?state=first-run`, { w: 1440, h: 900 });
    const { page, external } = session;
    await page.locator('#attach-button').click();
    await page.locator('#pdf-input').setInputFiles(path.resolve('data/pdf/mars-science-laboratory.pdf'));
    await page.getByRole('button', { name: 'Open sample pages' }).waitFor({ state: 'visible', timeout: 5000 }).catch(() => undefined);
    const filename = await page.locator('#attachment-name').innerText();
    const status = await page.locator('#composer-status').innerText();
    const requestsBeforeSubmit = await page.evaluate(() => performance.getEntriesByType('resource').filter(entry => ['fetch','xmlhttprequest'].includes(entry.initiatorType)).map(entry=>entry.name));
    const openAction = page.getByRole('button', { name: 'Open sample pages' });
    record('F1-D', 'checked-in sample selection exposes an explicit reading action', filename === 'mars-science-laboratory.pdf' && status === '' && await openAction.isVisible() && external.length === 0, `filename=${filename}; status=${JSON.stringify(status)}; openAction=${await openAction.isVisible()}; no ingestion handler is present`);
    if (await openAction.isVisible()) await openAction.click();
    const selectedReading = await page.evaluate(() => ({ state: document.body.dataset.state, title: document.querySelector('#sheet-title').textContent, page: document.querySelector('#page-count').textContent.trim(), open: !document.querySelector('#pdf-sheet').hidden, selected: document.querySelector('#attachment-name').textContent.trim() }));
    record('F1-D', 'sample reading progress follows explicit selection and remains tied to the filename', selectedReading.state === 'reading-progress' && selectedReading.open && selectedReading.page === '3 / 4' && selectedReading.selected === filename, JSON.stringify(selectedReading));
    await page.locator('#question-input').fill('A local question');
    await page.locator('#send-button').click();
    const explicitNoSend = await page.locator('#composer-status').innerText();
    const requestCountAfter = await page.evaluate(() => performance.getEntriesByType('resource').filter(entry => ['fetch','xmlhttprequest'].includes(entry.initiatorType)).length);
    record('C14', 'non-scripted question does not claim generation or send a request', explicitNoSend === 'No sample answer is scripted for this question.' && requestCountAfter === requestsBeforeSubmit.length && external.length === 0, `status=${JSON.stringify(explicitNoSend)}; resourceEntriesBefore=${requestsBeforeSubmit.length}; after=${requestCountAfter}; external=${external.length}`);
    await page.locator('#pdf-input').setInputFiles({ name: 'private-report.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 local placeholder') });
    record('F1-D', 'arbitrary PDF selection remains filename-only and is not parsed as the sample', await page.locator('#attachment-name').innerText() === 'private-report.pdf' && await page.getByRole('button', { name: 'Open sample pages' }).count() === 0 && await page.locator('#page-count').innerText() === '3 / 4', `filename=${await page.locator('#attachment-name').innerText()}; sampleActionCount=${await page.getByRole('button', { name: 'Open sample pages' }).count()}; currentPage=${await page.locator('#page-count').innerText()}`);
    await page.locator('#pdf-input').setInputFiles({ name: 'mars-science-laboratory.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 different same-named file') });
    await page.waitForTimeout(80);
    record('F1-D', 'a same-named but different PDF cannot open the bundled sample pages', await page.locator('#attachment-name').innerText() === 'mars-science-laboratory.pdf' && await page.getByRole('button', { name: 'Open sample pages' }).count() === 0, `filename=${await page.locator('#attachment-name').innerText()}; sampleActionCount=${await page.getByRole('button', { name: 'Open sample pages' }).count()}; exact sample SHA-256 required`);
    await session.context.close();
  }
  {
    const session = await makePage(browser, `${entry}?state=first-run`, { w: 390, h: 844 });
    const { page } = session;
    await page.locator('#attach-button').click();
    await page.locator('#pdf-input').setInputFiles(path.resolve('data/pdf/mars-science-laboratory.pdf'));
    const action = page.getByRole('button', { name: 'Open sample pages' });
    await action.waitFor({ state: 'visible', timeout: 5000 });
    const mobileName = await page.locator('#attachment-name').evaluate(node => ({ text:node.textContent, scrollWidth:node.scrollWidth, clientWidth:node.clientWidth, rect:node.getBoundingClientRect().toJSON() }));
    const actionBox = await action.boundingBox();
    record('F1-D', 'mobile attachment shows the full sample filename and a reachable 44px action', mobileName.text === 'mars-science-laboratory.pdf' && mobileName.scrollWidth <= mobileName.clientWidth && actionBox.height >= 44 && actionBox.y >= 0 && actionBox.y + actionBox.height <= 844, `name=${JSON.stringify(mobileName)}; action=${JSON.stringify(actionBox)}`);
    await session.context.close();
  }

  // CSS/JS motion, connector absence, banned visual forms, and actual computed typography.
  {
    const css = await fs.readFile(path.join(root, 'assets/reading-desk.css'), 'utf8');
    const html = await fs.readFile(path.join(root, 'proof-static-not-functional.html'), 'utf8');
    const badTransition = [...css.matchAll(/\btransition(?:-[a-z]+)?\s*:\s*([^;{}]+)/giu)].filter(match => /\b(?:left|top|width|height)\b/iu.test(match[1]));
    const keyframes = [];
    for (const match of css.matchAll(/@keyframes\s+[\w-]+\s*\{/giu)) {
      const open = match.index + match[0].lastIndexOf('{');
      let depth=0, end=open;
      for (; end<css.length; end++) { if (css[end]==='{') depth++; else if (css[end]==='}' && --depth===0) { end++; break; } }
      keyframes.push(css.slice(open,end));
    }
    const badKeyframes = keyframes.filter(block=>/\b(?:left|top|width|height)\s*:/iu.test(block));
    const jsMotion = [...html.matchAll(/\.animate\s*\(([^;]+)\)/gu)].filter(match=>/\b(?:left|top|width|height)\b/iu.test(match[1]));
    record('C3', 'CSS and JavaScript animate only transform/opacity', badTransition.length===0 && badKeyframes.length===0 && jsMotion.length===0, `geometry transitions=${badTransition.length}; geometry keyframes=${badKeyframes.length}; geometry JS animations=${jsMotion.length}`);
    const forbiddenDom = await browser.newPage();
    await forbiddenDom.goto(`${entry}?state=answered-closed`, { waitUntil: 'networkidle' });
    const connectorNodes = await forbiddenDom.locator('[id*="leader" i],[class*="leader" i],[id*="connector" i],[class*="connector" i],svg path[data-connector]').count();
    const cardStyles = await forbiddenDom.evaluate(() => [...document.querySelectorAll('.assistant-message,.answer-paragraph')].map(node=>{const s=getComputedStyle(node);return {background:s.backgroundColor,borderWidth:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth].map(parseFloat),borderStyle:[s.borderTopStyle,s.borderRightStyle,s.borderBottomStyle,s.borderLeftStyle]};}));
    const boxed = cardStyles.some(item=>item.background!=='rgba(0, 0, 0, 0)' || item.borderWidth.some(width=>width>0&&item.borderStyle[0]!=='none'));
    record('C8/C12/C16', 'assistant remains unboxed and no connector exists in DOM', connectorNodes===0 && !boxed, `connectorNodes=${connectorNodes}; assistantStyles=${JSON.stringify(cardStyles)}`);
    const bannedVisual = await forbiddenDom.evaluate(() => {
      const buttons=[...document.querySelectorAll('button')].filter(el=>el.getClientRects().length);
      const blueButton=buttons.some(el=>{const m=getComputedStyle(el).backgroundColor.match(/\d+/g)?.map(Number)||[];return m.length>=3&&m[2]>m[0]*1.1&&m[2]>m[1]*1.05;});
      const cardLike=[...document.querySelectorAll('#thread *')].filter(el=>{
        if(el.matches('img,svg,.citation-mark,.citation-chip,.suggestion,.user-message')||el.closest('.citation-popover')) return false;
        const s=getComputedStyle(el),r=el.getBoundingClientRect();
        return s.backgroundColor!=='rgba(0, 0, 0, 0)'&&r.width>300&&r.height>120&&parseFloat(s.borderTopLeftRadius)>16;
      }).map(el=>el.className||el.tagName);
      return {blueButton,largeLightCard:cardLike,centeredSerifHero:!!document.querySelector('h1,.hero-title'),stepper:!!document.querySelector('.stepper,[aria-label*="step" i]')};
    });
    const hasGradient=/\b(?:linear|radial|conic)-gradient\s*\(/iu.test(css);
    record('C8', 'banned visual treatments are absent from the proof structure', !hasGradient&&!bannedVisual.blueButton&&!bannedVisual.largeLightCard.length&&!bannedVisual.centeredSerifHero&&!bannedVisual.stepper, `gradient=${hasGradient}; ${JSON.stringify(bannedVisual)}`);
    await forbiddenDom.close();
  }

  // Contrast measurements for tokens actually used in the proof.
  const contrasts = [
    ['warm text / dark room','#F0EBDD','#16140F'],
    ['muted text / conversation','#B9B3A6','#211F19'],
    ['quiet composer hint / composer','#B9B3A6','#25231D'],
    ['figure caption / raised panel','#DFD9CB','#29271F'],
    ['figure credit / raised panel','#B9B3A6','#29271F'],
    ['PDF ink / warm paper','#211F19','#EEE8D9'],
    ['citation numeral / lime','#18170F','#D7FF3A'],
    ['status text / dark room','#B9B3A6','#16140F'],
  ];
  for (const [label, foreground, background] of contrasts) {
    const value = ratios(foreground, background);
    record('C6', `contrast ${label}`, value >= 4.5, `${value.toFixed(2)}:1 (${foreground} / ${background})`);
  }

  // Real rendering checks for both comparison pages and three local logo marks.
  {
    const fontPage = await makePage(browser, `${origin}/font-comparison.html`, { w: 1440, h: 900 });
    const fonts = await fontPage.page.evaluate(() => ({
      candidates: ['Newsreader','SourceSerif4','Instrument','Geist'].map(name=>({name,loaded:document.fonts.check(`18px "${name}"`)})),
      measures: [...document.querySelectorAll('[data-measure]')].map(el=>({family:el.dataset.measure,summary:el.textContent.trim()})),
    }));
    record('C17/C7', 'four candidate fonts are bundled and comparison specimens render locally', fonts.candidates.every(candidate=>candidate.loaded) && fonts.measures.length===2 && fonts.measures.every(item=>item.summary.includes('Rendered family')) && fontPage.external.length===0 && fontPage.failedResponses.length===0, JSON.stringify({...fonts,external:fontPage.external,failedResponses:fontPage.failedResponses}));
    await fontPage.context.close();
    const logoPage = await makePage(browser, `${origin}/logo-comparison.html`, { w: 1440, h: 900 });
    const logos = await logoPage.page.evaluate(() => ({
      candidates: [...document.querySelectorAll('.candidate h2')].map(el=>el.textContent.trim()),
      renderedUses: [...document.querySelectorAll('svg.mark use')].length,
      sampleSizes: [...new Set([...document.querySelectorAll('.sample svg.mark')].map(el=>Math.round(el.getBoundingClientRect().width)))].sort((a,b)=>a-b),
      lockupMarks: document.querySelectorAll('.lockup svg.mark').length,
      surfaceCount: document.querySelectorAll('.surface').length,
      lockupCount: document.querySelectorAll('.lockup').length,
      externalImages: [...document.images].filter(image=>new URL(image.src).origin!==origin).length,
    }));
    const markFiles = ['mark-a-open-passage.svg','mark-b-reading-current.svg','mark-c-section-knot.svg'];
    const markContents = await Promise.all(markFiles.map(name=>fs.readFile(path.join(root,'assets','marks',name),'utf8')));
    const markValid = markContents.every(text=>text.includes('viewBox="0 0 24 24"') && text.includes('id="mark"') && !/gradient/iu.test(text));
    const silhouetteHashes = [];
    for (let index = 0; index < 3; index += 1) {
      const png = await logoPage.page.locator('.candidate').nth(index).locator('.sample').first().screenshot();
      silhouetteHashes.push(createHash('sha256').update(png).digest('hex'));
    }
    const distinctAt16 = new Set(silhouetteHashes).size === 3;
    record('C18/C7', 'three original logo marks render locally and have distinct 16px monochrome silhouettes', logos.candidates.length===3 && logos.renderedUses===42 && [16,32,64].every(size=>logos.sampleSizes.includes(size)) && logos.surfaceCount===6 && logos.lockupCount===6 && logos.lockupMarks===6 && markValid && distinctAt16 && logoPage.external.length===0 && logoPage.failedResponses.length===0, JSON.stringify({...logos,markFiles,markValid,distinctAt16,silhouetteHashes,external:logoPage.external,failedResponses:logoPage.failedResponses}));
    await logoPage.context.close();
  }

  // Citation claim text and exact source metadata stay attached to verified, distinct page targets.
  {
    const session = await makePage(browser, `${entry}?state=answered-closed`, { w: 1440, h: 900 });
    const mapping = await session.page.evaluate(() => ({
      citations: [...document.querySelectorAll('.citation-chip')].map(el=>el.dataset.sourceId),
      targets: window.__sources.filter(source=>source.cited).map(source=>({id:source.id,page:source.page,quoteWords:(source.quote.match(/[\p{L}\p{N}]+/gu)||[]).length,boxes:source.boxes.length,method:source.method})),
      figureAlt: document.querySelector('.inline-figure img')?.alt,
      figureCredit: document.querySelector('.figure-credit')?.textContent,
    }));
    const uniqueTargets = new Set(mapping.citations).size === 3 && mapping.citations.every(id=>mapping.targets.some(target=>target.id===id));
    record('C1/C2', 'three inline citations map to distinct quoted verified page passages', uniqueTargets && mapping.targets.every(target=>target.page===2&&target.quoteWords<=25&&target.boxes>0), JSON.stringify(mapping));
    record('C1', 'figure includes original image description and adjacent NASA credit', !!mapping.figureAlt && mapping.figureCredit==='NASA/JPL-Caltech/MSSS', `alt=${JSON.stringify(mapping.figureAlt)}; credit=${mapping.figureCredit}`);
    await session.context.close();
  }

  // Capture dimensions are verified after the capture step.
  const requiredShots = [];
  for (const state of states) for (const {w} of viewports) requiredShots.push(`${state}-${w}.png`);
  requiredShots.push('citation-popover-desktop.png','citation-popover-mobile.png','model-missing-pdf-open.png','font-comparison.png','logo-comparison.png','attachment-selected-1440.png','attachment-selected-390.png','attachment-reading-progress-1440.png','attachment-reading-progress-390.png','first-run-to-answered-1440.png','answer-visible-1024.png','source-link-caption-390.png','source-stream-flow-390.png','source-john-klein-water-390.png');
  const shotResults = [];
  for (const name of requiredShots) {
    const full = path.join(root,'shots',name);
    try {
      const stat = await fs.stat(full);
      const page = await browser.newPage();
      const dimensions = await page.evaluate(async (url) => new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve({width:image.naturalWidth,height:image.naturalHeight});image.onerror=reject;image.src=url; }), `${origin}/shots/${name}`);
      await page.close();
      shotResults.push({name,bytes:stat.size,...dimensions});
    } catch (error) { shotResults.push({name,error:error.message}); }
  }
  const requiredCount = requiredShots.length;
  record('C1/C2/C7/C18', 'all required screenshots exist at exact viewport dimensions', shotResults.length===requiredCount && shotResults.every(shot=>shot.bytes>0&&!shot.error&&((shot.name.endsWith('-1440.png')&&shot.width===1440&&shot.height===900)||(shot.name.endsWith('-1024.png')&&shot.width===1024&&shot.height===768)||(shot.name.endsWith('-390.png')&&shot.width===390&&shot.height===844)||(!/-(?:1440|1024|390)\.png$/u.test(shot.name)&&shot.width>0&&shot.height>0))), `${shotResults.length}/${requiredCount}; ${JSON.stringify(shotResults)}`);

  // Per-state copy and interface facts for the requested 1440px review.
  lines.push('', 'VISIBLE COPY WORD COUNTS AT 1440PX (document body, user-authored question, answer, source quotation/caption/credit excluded):');
  for (const state of states) {
    const result = stateResults.get(`${state}:1440`);
    lines.push(`COPY | ${state} | ${result?.wordCount ?? 'unavailable'} words | ${result ? JSON.stringify(result.text) : 'state did not render'}`);
  }
  const total = passed + failures;
  lines.push('', `TOTAL: ${passed}/${total} automated checks passed; failures=${failures}.`, 'Manual screenshot review remains separate from these measurements. Production application behavior is unverified by this static proof.');
} finally {
  await browser.close();
}
const output = lines.join('\n') + '\n';
await fs.writeFile(path.join(root, 'CHECK-RESULTS.txt'), output, 'utf8');
process.stdout.write(output);
if (failures) process.exitCode = 1;
