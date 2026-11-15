import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';

const require = createRequire(path.join(process.env.APPDATA || '', 'npm', 'node_modules', '@playwright', 'cli', 'package.json'));
const { chromium } = require('playwright');
const root = path.resolve('docs/design/m9-a-proof');
const base = 'http://127.0.0.1:8765/proof-static-not-functional.html';
const viewports = [{ w: 1440, h: 900 }, { w: 1024, h: 768 }, { w: 390, h: 844 }];
const states = ['drop', 'reading', 'answered', 'model-missing'];
const lines = ['M9-A static proof verification', 'Classification: PASS_SYNTHETIC applies only to the rendered HTML composition and local interaction checks; no backend, retrieval, or generation was tested.', ''];
let failures = 0;
const record = (name, pass, detail) => {
  lines.push(`${pass ? 'PASS' : 'FAIL'} | ${name} | ${detail}`);
  if (!pass) failures += 1;
};
const ratio = (fg, bg) => {
  const channel = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (hex) => {
    const raw = hex.replace('#', '');
    return 0.2126 * channel(parseInt(raw.slice(0, 2), 16)) + 0.7152 * channel(parseInt(raw.slice(2, 4), 16)) + 0.0722 * channel(parseInt(raw.slice(4, 6), 16));
  };
  const a = lum(fg), b = lum(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};
const expectedEvidence = [
  {
    id: 'link-caption',
    label: '1 · p.02',
    passage: 'A rock outcrop called Link shows signs of being formed by the deposition of water.',
    boxes: [[46.08,217.376,282.285,227.6],[46.08,229.376,74.6919,239.6]],
  },
  {
    id: 'stream-flow',
    label: '2 · p.02',
    passage: 'The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.',
    boxes: [[44.5,584.66,274.202,596.48],[44.5,600.16,286.421,611.98],[44.5,615.66,133.78,627.48]],
  },
  {
    id: 'john-klein-water',
    label: '3 · p.02',
    passage: 'first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,',
    boxes: [[314.64,139.4,559.882,151.22],[314.64,154.9,547.671,166.72],[314.64,170.4,560.84,182.22],[314.64,185.9,423.73,197.72]],
  },
];
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  for (const state of states) {
    for (const { w, h } of viewports) {
      const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1, reducedMotion: 'no-preference' });
      const external = [], badResponses = [], pageErrors = [];
      page.on('request', request => { if (new URL(request.url()).origin !== 'http://127.0.0.1:8765') external.push(request.url()); });
      page.on('response', response => { if (response.status() >= 400) badResponses.push(`${response.status()} ${response.url()}`); });
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.addInitScript(() => {
        window.__m9aLayoutShift = 0;
        if (PerformanceObserver.supportedEntryTypes.includes('layout-shift')) {
          const observer = new PerformanceObserver(list => {
            for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__m9aLayoutShift += entry.value;
          });
          observer.observe({ type: 'layout-shift', buffered: true });
        }
      });
      await page.goto(`${base}?state=${state}`, { waitUntil: 'networkidle' });
      await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(img => img.decode().catch(() => undefined))); });
      const label = `${state} ${w}x${h}`;
      const metrics = await page.evaluate(() => {
        const rect = (selector) => { const el = document.querySelector(selector); if (!el || el.hidden) return null; const r = el.getBoundingClientRect(); return { x:r.x, y:r.y, right:r.right, bottom:r.bottom, width:r.width, height:r.height }; };
        const intersect = (a,b) => !!a && !!b && a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y;
        const bodySelectors = ['.body-copy','.conversation-heading p','.suggestion','.question-bubble','.evidence-sentence','.figure-caption','.source-card p','.empty-desk span','#composer-placeholder'];
        const bodyText = bodySelectors.flatMap(s => [...document.querySelectorAll(s)]).filter(el => !el.closest('[hidden]')).map(el => ({ selector:el.className || el.tagName, px:parseFloat(getComputedStyle(el).fontSize) }));
        const anchors = [...document.querySelectorAll('a')].filter(el => !el.closest('[hidden]')).map(el => { const r=el.getBoundingClientRect(); return { label:el.getAttribute('aria-label') || el.textContent.trim(), width:r.width, height:r.height }; });
        const targets = [...document.querySelectorAll('.settings-mark,.control-glyph,a.source-chip')].filter(el => !el.closest('[hidden]')).map(el => { const r=el.getBoundingClientRect(); return { label:el.getAttribute('aria-label') || el.className || el.textContent.trim(), width:r.width, height:r.height }; });
        const images = [...document.images].filter(el => !el.closest('[hidden]')).map(el => ({ src:el.getAttribute('src'), loaded:el.complete && el.naturalWidth > 0 }));
        const conversation = rect('.conversation'), reader = rect('.reader');
        const structural = ['.topbar','.desk','.conversation','.reader','.reader-toolbar','.page-area','.page-strip','.composer'].map(s => [s,rect(s)]);
        return {
          scrollWidth:document.documentElement.scrollWidth, innerWidth:window.innerWidth, innerHeight:window.innerHeight, documentHeight:document.documentElement.scrollHeight, layoutShift:window.__m9aLayoutShift,
          bodyText, anchors, targets, images,
          fontSans:document.fonts.check('16px "Plex Sans"'), fontMono:document.fonts.check('12px "Plex Mono"'),
          overlapConversationReader:intersect(conversation,reader), structural,
          visibleText:document.body.innerText.toLowerCase(),
          pageImage:rect('#page-image'), figure:rect('.figure-card'), highlight:rect('#evidence-highlight'),
          answerCount:document.querySelectorAll('.evidence-sentence').length,
          readerTitle:document.querySelector('#reader-title')?.textContent.trim(),
          readerSubtitle:document.querySelector('#reader-subtitle')?.textContent.trim(),
          readerIdentityHidden:document.querySelector('.reader-identity')?.hidden,
          readerControlsHidden:document.querySelector('.reader-controls')?.hidden,
          pageCount:document.querySelector('#page-count')?.textContent.trim(),
          statusText:document.querySelector('#status-text')?.textContent.trim(),
        };
      });
      record(`${label}: same-origin requests`, external.length === 0, external.length ? external.join(', ') : 'all browser requests used http://127.0.0.1:8765');
      record(`${label}: local asset responses`, badResponses.length === 0, badResponses.length ? badResponses.join('; ') : 'all requested files returned below 400');
      record(`${label}: no browser exceptions`, pageErrors.length === 0, pageErrors.length ? pageErrors.join('; ') : 'no page errors');
      record(`${label}: no horizontal overflow`, metrics.scrollWidth <= metrics.innerWidth + 1, `scrollWidth=${metrics.scrollWidth}px, viewport=${metrics.innerWidth}px`);
      record(`${label}: proof-render layout shift`, metrics.layoutShift !== null && metrics.layoutShift < 0.01, `CLS=${metrics.layoutShift}`);
      record(`${label}: major zones do not overlap`, !metrics.overlapConversationReader, 'conversation and reader bounds are disjoint');
      const out = metrics.structural.filter(([name,r]) => r && (r.x < -1 || r.right > w+1 || r.y < -1 || (w !== 390 && name !== '.desk' && r.bottom > h+1) || (w === 390 && r.bottom > metrics.documentHeight+1)));
      record(`${label}: major layout bounds and mobile scroll access`, out.length === 0 && (w !== 390 || metrics.documentHeight >= metrics.innerHeight), out.length ? JSON.stringify(out) : (w === 390 ? `no horizontal escape; reader remains in vertical document flow (${metrics.documentHeight}px page)` : 'top bar, split panes, reader controls and composer remain in viewport'));
      const undersized = metrics.bodyText.filter(t => t.px < 16);
      record(`${label}: body and evidence copy >=16px`, undersized.length === 0, undersized.length ? JSON.stringify(undersized) : `checked ${metrics.bodyText.length} visible body/evidence text nodes`);
      const smallTargets = metrics.anchors.filter(t => t.width < 44 || t.height < 44);
      record(`${label}: interactive citation target >=44px`, smallTargets.length === 0, smallTargets.length ? JSON.stringify(smallTargets) : `checked ${metrics.anchors.length} visible links`);
      const undersizedTargets = metrics.targets.filter(target => target.width < 44 || target.height < 44);
      record(`${label}: visible control target boxes >=44px`, undersizedTargets.length === 0, undersizedTargets.length ? JSON.stringify(undersizedTargets) : `checked ${metrics.targets.length} shell/citation targets`);
      const unloaded = metrics.images.filter(i => !i.loaded);
      const assetsOk = unloaded.length === 0 && (state === 'drop' ? metrics.images.length === 0 : metrics.images.length > 0);
      record(`${label}: visible document assets loaded`, assetsOk, unloaded.length ? JSON.stringify(unloaded) : `${metrics.images.length} visible local images loaded`);
      record(`${label}: bundled fonts loaded`, metrics.fontSans && metrics.fontMono, `IBM Plex Sans=${metrics.fontSans}; IBM Plex Mono=${metrics.fontMono}`);
      const forbiddenStrings = ['preview','illustrative','not functional'].filter(term => metrics.visibleText.includes(term));
      record(`${label}: no proof disclaimer in visible UI`, forbiddenStrings.length === 0, forbiddenStrings.length ? forbiddenStrings.join(', ') : 'none of the restricted terms appear in rendered UI text');
      if (state === 'answered') {
        const sentence = page.locator('.evidence-sentence').first();
        const line = page.locator('#leader-line');
        if (w === 390) await sentence.focus(); else await sentence.hover();
        await page.waitForFunction(() => Number.parseFloat(getComputedStyle(document.querySelector('#leader-line')).opacity) > 0.8);
        const activeLeader = await line.evaluate(el => el.classList.contains('is-visible') && Number.parseFloat(getComputedStyle(el).opacity) > 0.8);
        const activeMarkCount = await page.locator('#evidence-highlight .evidence-target:not([hidden]) i').count();
        const highlightVisible = await page.locator('#evidence-highlight').evaluate(el => !el.hidden && el.querySelectorAll('.evidence-target:not([hidden])').length === 1);
        record(`${label}: hover/focus leader and evidence highlight`, activeLeader && highlightVisible, `mode=${w === 390 ? 'keyboard focus' : 'pointer hover'}; leader=${activeLeader}; active highlighted lines=${activeMarkCount}`);
        if (w !== 390) {
          await page.mouse.move(5, h - 5);
          await page.waitForTimeout(280);
          const removed = await line.evaluate(el => !el.classList.contains('is-visible') || getComputedStyle(el).opacity === '0');
          record(`${label}: leader removed after pointer leaves`, removed, `lineVisible=${!removed}`);
        } else {
          lines.push(`N/A | ${label}: pointer hover on touch viewport | touch viewport has no hover input; keyboard focus and dismissal are tested below`);
        }
        await page.keyboard.press('Tab');
        let focused = await page.evaluate(() => document.activeElement?.classList.contains('evidence-sentence'));
        if (!focused) await sentence.focus();
        await page.waitForTimeout(260);
        const keyboardVisible = await line.evaluate(el => el.classList.contains('is-visible') && Number.parseFloat(getComputedStyle(el).opacity) > 0.8);
        record(`${label}: keyboard focus shows leader`, keyboardVisible, `focusedAnswerSentence=${focused || await page.evaluate(() => document.activeElement?.classList.contains('evidence-sentence'))}; leader=${keyboardVisible}`);
        const visibleFocus = await sentence.evaluate(el => getComputedStyle(el).outlineStyle !== 'none' && parseFloat(getComputedStyle(el).outlineWidth) >= 2);
        record(`${label}: keyboard focus ring visible`, visibleFocus, `outline=${await sentence.evaluate(el => getComputedStyle(el).outlineWidth)} ${await sentence.evaluate(el => getComputedStyle(el).outlineColor)}`);
        await page.evaluate(() => { const composer = document.querySelector('.composer-box'); composer.setAttribute('tabindex','-1'); composer.focus(); });
        await page.waitForTimeout(280);
        const keyboardRemoved = await line.evaluate(el => !el.classList.contains('is-visible') || getComputedStyle(el).opacity === '0');
        record(`${label}: leader removed after keyboard leaves sentence`, keyboardRemoved, `lineVisible=${!keyboardRemoved}`);

        const mappings = await page.locator('.evidence-sentence').evaluateAll((nodes, expected) => {
          const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
          return nodes.map(node => {
            const id = node.dataset.evidenceTarget;
            const target = [...document.querySelectorAll('.evidence-target')].find(item => item.dataset.evidenceTarget === id);
            const chip = node.querySelector('.source-chip');
            const text = chip ? [...chip.childNodes].find(item => item.nodeType === Node.TEXT_NODE)?.textContent.trim() : '';
            const marks = [...(target?.querySelectorAll('i') || [])];
            const boxes = marks.map(mark => (mark.dataset.pdfBox || '').split(',').map(Number));
            const geometry = marks.map(mark => {
              const [x0,y0,x1,y1] = (mark.dataset.pdfBox || '').split(',').map(Number);
              return [parseFloat(mark.style.left) * 6.12, parseFloat(mark.style.top) * 7.92, (parseFloat(mark.style.left) + parseFloat(mark.style.width)) * 6.12, (parseFloat(mark.style.top) + parseFloat(mark.style.height)) * 7.92, x0,y0,x1,y1];
            });
            return { id, label:text, href:chip?.getAttribute('href'), page:target?.dataset.page, passage:normalize(target?.dataset.sourcePassage), marks:marks.length, boxes, geometry, expected:expected.find(item => item.id === id) || null };
          });
        }, expectedEvidence);
        const mappedClaims = mappings.length === expectedEvidence.length && mappings.every((item, index) => item.expected && item.id === item.expected.id && item.label === item.expected.label && item.href === `#evidence-target-${item.id}` && item.page === '2' && item.passage === item.expected.passage && item.marks === item.expected.boxes.length);
        record(`${label}: claims map to distinct source passages`, mappedClaims, JSON.stringify(mappings.map(({id,label,page,marks,passage}) => ({id,label,page,marks,passage}))));
        const coordinateErrors = mappings.flatMap(item => item.geometry.flatMap((box, markIndex) => {
          const expected = item.expected?.boxes[markIndex];
          return !expected || box.some((value, index) => Math.abs(value - expected[index]) > 0.12) ? [{id:item.id, mark:markIndex, actual:box, expected}] : [];
        }));
        record(`${label}: highlight bounds match verified PDF coordinates`, mappings.length === expectedEvidence.length && coordinateErrors.length === 0, coordinateErrors.length ? JSON.stringify(coordinateErrors) : 'page-2 highlight boxes match the independently extracted Poppler point bounds');

        for (const expected of expectedEvidence) {
          const claim = page.locator(`.evidence-sentence[data-evidence-target="${expected.id}"]`);
          if (await claim.count() !== 1) {
            record(`${label}: ${expected.id} selects its own highlight and leader endpoint`, false, 'expected one claim with this evidence identity; none was mapped');
            if (w === 390) record(`${label}: ${expected.id} mobile connector clears content and passage is readable`, false, 'claim-specific source target is missing');
            continue;
          }
          if (w === 390) await claim.focus(); else await claim.hover();
          await page.waitForFunction(id => {
            const path = document.querySelector('#leader-line');
            const target = [...document.querySelectorAll('.evidence-target')].find(item => item.dataset.evidenceTarget === id);
            const anchor = target?.querySelector('[data-leader-anchor]');
            const rect = anchor?.getBoundingClientRect();
            const numbers = (path?.getAttribute('d') || '').match(/-?(?:\d+\.?\d*|\.\d+)/g)?.map(Number) || [];
            if (document.querySelector('#evidence-highlight')?.dataset.activeTarget !== id || path?.dataset.targetId !== id || !path?.classList.contains('is-visible') || !rect || numbers.length < 2) return false;
            const end = path.dataset.route === 'mobile-gutter' ? [numbers[4],numbers[3]] : numbers.slice(-2);
            return Math.hypot(end[0] - (path.dataset.route === 'mobile-gutter' ? rect.right : rect.left), end[1] - (rect.top + rect.height / 2)) < 2;
          }, expected.id);
          const linkState = await page.evaluate(id => {
            const sentence = document.querySelector(`.evidence-sentence[data-evidence-target="${id}"]`);
            const chip = sentence?.querySelector('.source-chip');
            const target = [...document.querySelectorAll('.evidence-target')].find(item => item.dataset.evidenceTarget === id);
            const anchor = target?.querySelector('[data-leader-anchor]');
            const path = document.querySelector('#leader-line');
            const nums = (path?.getAttribute('d') || '').match(/-?(?:\d+\.?\d*|\.\d+)/g)?.map(Number) || [];
            const end = path?.dataset.route === 'mobile-gutter' ? [nums[4],nums[3]] : nums.slice(-2);
            const chipRect = chip?.getBoundingClientRect();
            const anchorRect = anchor?.getBoundingClientRect();
            const pageArea = document.querySelector('.page-area')?.getBoundingClientRect();
            const figure = document.querySelector('.figure-card')?.getBoundingClientRect();
            const sentenceRect = sentence?.getBoundingClientRect();
            const visibleTargets = [...document.querySelectorAll('.evidence-target')].filter(item => !item.hidden).length;
            const marks = [...(target?.querySelectorAll('i') || [])].map(item => item.getBoundingClientRect());
            return {
              activeTarget:document.querySelector('#evidence-highlight')?.dataset.activeTarget,
              lineTarget:path?.dataset.targetId,
              sourceTarget:chip?.dataset.sourceId,
              targetVisible:!!target && !target.hidden && visibleTargets === 1,
              lineVisible:path?.classList.contains('is-visible'),
              route:path?.dataset.route,
              routeNumbers:nums,
              anchorBounds:anchorRect && {left:anchorRect.left,right:anchorRect.right,top:anchorRect.top,bottom:anchorRect.bottom},
              pageAreaBounds:pageArea && {left:pageArea.left,right:pageArea.right,top:pageArea.top,bottom:pageArea.bottom},
              endpointError:anchorRect && end.length === 2 ? Math.hypot(end[0] - (innerWidth <= 640 ? anchorRect.right : anchorRect.left), end[1] - (anchorRect.top + anchorRect.height / 2)) : Infinity,
              mobile:innerWidth <= 640 ? {
                anchorInViewport:!!anchorRect && anchorRect.left >= -1 && anchorRect.right <= innerWidth + 1 && marks.length > 0 && marks.every(rect => rect.top >= Math.max(0,pageArea.top) - 1 && rect.bottom <= innerHeight + 1),
                answerAndFigureVisible:!!sentenceRect && sentenceRect.top >= 0 && sentenceRect.bottom <= innerHeight && !!figure && figure.top >= 0 && figure.bottom <= innerHeight,
                sourceStartMatchesChip:chipRect && nums.length === 5 && Math.abs(nums[0] - chipRect.right) < 2 && Math.abs(nums[1] - (chipRect.top + chipRect.height / 2)) < 2,
                gutterOutsideAnswerAndFigure:nums.length === 5 && nums[2] > Math.max(sentence?.closest('.answer')?.getBoundingClientRect().right || 0, figure?.right || 0),
                shortFinalGutter:nums.length === 5 && nums[2] - nums[4] <= 42,
              } : null,
            };
          }, expected.id);
          const linkMatches = linkState.activeTarget === expected.id && linkState.lineTarget === expected.id && linkState.sourceTarget === expected.id && linkState.targetVisible && linkState.lineVisible && linkState.endpointError < 2;
          record(`${label}: ${expected.id} selects its own highlight and leader endpoint`, linkMatches, JSON.stringify(linkState));
          if (w === 390) {
            const mobile = linkState.mobile || {};
            const mobileOk = linkState.route === 'mobile-gutter' && mobile.anchorInViewport && mobile.answerAndFigureVisible && mobile.sourceStartMatchesChip && mobile.gutterOutsideAnswerAndFigure && mobile.shortFinalGutter;
            record(`${label}: ${expected.id} mobile connector clears content and passage is readable`, mobileOk, JSON.stringify(mobile));
          }
        }
      }
      if (state === 'model-missing') {
        const sourceState = await page.evaluate(() => {
          const target = document.querySelector('.evidence-target[data-evidence-target="stream-flow"]');
          const area = document.querySelector('.page-area').getBoundingClientRect();
          const marks = [...(target?.querySelectorAll('i') || [])].map(mark => mark.getBoundingClientRect());
          return {
            targetVisible:!!target && !target.hidden && document.querySelector('#evidence-highlight')?.dataset.activeTarget === 'stream-flow',
            passage:target?.dataset.sourcePassage || '',
            marks:marks.length,
            marksInMobileReader:innerWidth !== 390 || marks.length === 3 && marks.every(mark => mark.top >= Math.max(0,area.top) - 1 && mark.bottom <= innerHeight + 1),
            leaderHidden:!document.querySelector('#leader-line')?.classList.contains('is-visible'),
          };
        });
        const present = metrics.answerCount === 0 && !!metrics.pageImage && !!metrics.figure && !!metrics.highlight && sourceState.targetVisible && sourceState.passage.includes('evidence for stream flow') && sourceState.marks === 3 && sourceState.marksInMobileReader && sourceState.leaderHidden;
        record(`${label}: missing model preserves the verified source without a fake answer`, present, JSON.stringify({answers:metrics.answerCount,page:!!metrics.pageImage,figure:!!metrics.figure,...sourceState}));
      }
      if (state === 'drop') {
        const empty = await page.locator('#empty-desk').isVisible();
        record(`${label}: empty desk shown`, empty, `emptyDocumentOutline=${empty}`);
        const staleSource = !!metrics.readerTitle || !!metrics.readerSubtitle || !!metrics.pageCount || !metrics.readerIdentityHidden || !metrics.readerControlsHidden;
        record(`${label}: empty reader has no stale document identity or page controls`, !staleSource, JSON.stringify({title:metrics.readerTitle,subtitle:metrics.readerSubtitle,pageCount:metrics.pageCount,identityHidden:metrics.readerIdentityHidden,controlsHidden:metrics.readerControlsHidden}));
      }
      const expectedStatus = state === 'model-missing' ? 'Ollama unavailable' : 'Model ready';
      record(`${label}: status describes the answer model`, metrics.statusText === expectedStatus, `visible status=${JSON.stringify(metrics.statusText)}; expected=${JSON.stringify(expectedStatus)}`);
      const reducePage = await browser.newPage({ viewport:{width:w,height:h}, reducedMotion:'reduce' });
      await reducePage.goto(`${base}?state=answered`, { waitUntil:'networkidle' });
      const reduced = await reducePage.evaluate(() => {
        const anim = getComputedStyle(document.querySelector('.page-warmth')).animationName;
        const thumbAnim = getComputedStyle(document.querySelector('.page-thumb[aria-current="page"]'),'::after').animationName;
        const transition = getComputedStyle(document.querySelector('#leader-line')).transitionDuration;
        return { anim, thumbAnim, transition, pageVisible:!document.querySelector('#page-frame').hidden };
      });
      record(`${label}: reduced-motion keeps controls/content and removes motion`, reduced.anim === 'none' && reduced.thumbAnim === 'none' && reduced.transition.split(',').every(x=>parseFloat(x)===0) && reduced.pageVisible, JSON.stringify(reduced));
      await reducePage.locator('.evidence-sentence').first().focus();
      const reducedFocusWorks = await reducePage.locator('#leader-line').evaluate(el => el.classList.contains('is-visible') && Number.parseFloat(getComputedStyle(el).opacity) > 0.8);
      record(`${label}: reduced-motion keeps citation interaction`, reducedFocusWorks, `focused citation leader=${reducedFocusWorks}`);
      await reducePage.close();
      await page.close();
    }
  }
  const contrastPairs = [
    ['body text on conversation','#F0EBDD','#211F19'],
    ['secondary instructions on conversation','#D5CFBF','#211F19'],
    ['figure caption on raised panel','#E7E0D2','#29271F'],
    ['source passage on conversation','#E8E2D4','#211F19'],
    ['quiet composer hint on conversation','#A39D8E','#211F19'],
    ['reader secondary label on room','#918B7C','#16140F'],
    ['figure credit on raised panel','#B9B2A2','#29271F'],
    ['PDF ink on warm paper','#211F19','#EEE8D9'],
    ['active chip text on highlight','#19180F','#D7FF3A'],
    ['chip text on conversation','#D7FF3A','#211F19'],
    ['status text on room','#B7B09F','#16140F'],
  ];
  for (const [name,fg,bg] of contrastPairs) {
    const value = ratio(fg,bg);
    record(`contrast ${name}`, value >= 4.5, `${value.toFixed(2)}:1 (${fg} on ${bg})`);
  }
  const css = await fs.readFile(path.join(root,'assets/reading-desk.css'),'utf8');
  const geometry = ['left:','top:','width:','height:'];
  const transitionGeometry = css.split(';').map(part => {
    const colon = part.lastIndexOf(':');
    return { property:part.slice(0,colon).split('}').at(-1).trim().toLowerCase(), value:part.slice(colon+1).toLowerCase() };
  }).filter(item => (item.property === 'transition' || item.property.startsWith('transition-')) && ['left','top','width','height'].some(property => item.value.includes(property)));
  const keyframeBlocks = [];
  let scanAt = 0;
  while ((scanAt = css.indexOf('@keyframes',scanAt)) >= 0) {
    const open = css.indexOf('{',scanAt);
    if (open < 0) break;
    let depth = 0, end = open;
    for (; end < css.length; end++) { if (css[end] === '{') depth++; else if (css[end] === '}' && --depth === 0) { end++; break; } }
    keyframeBlocks.push(css.slice(open,end));
    scanAt = end;
  }
  const keyframeGeometry = keyframeBlocks.filter(block => geometry.some(property => block.includes(property)));
  const htmlContent = await fs.readFile(path.join(root,'proof-static-not-functional.html'),'utf8');
  const jsOpen = htmlContent.indexOf('<script>');
  const jsClose = htmlContent.indexOf('</script>',jsOpen);
  const jsSource = htmlContent.slice(jsOpen + 8,jsClose);
  const jsAnimationCalls = jsSource.match(/\.animate\s*\([\s\S]*?\)/g) || [];
  const jsGeometryMotion = jsAnimationCalls.filter(call => geometry.some(property => call.toLowerCase().includes(property)));
  const motionProblems = [...transitionGeometry.map(item => `${item.property}:${item.value}`), ...keyframeGeometry, ...jsGeometryMotion];
  const hasGradient = css.toLowerCase().includes('gradient(');
  const lowerCss = css.toLowerCase();
  const hasBluePrimary = lowerCss.includes('blue-primary') || (lowerCss.includes('blue') && lowerCss.includes('.primary'));
  const hasSerif = ['Georgia','Times New Roman','font-family:serif'].some(term => css.includes(term));
  const lowerHtml = htmlContent.toLowerCase();
  const hasStepper = lowerHtml.includes('stepper') || lowerHtml.includes('step-indicator');
  record('CSS and JS motion avoids left/top/width/height animation', motionProblems.length === 0, motionProblems.length ? JSON.stringify(motionProblems) : 'transitions use transform/opacity; keyframes use opacity/transform; JavaScript does not animate geometry');
  record('banned gradient absent', !hasGradient, `gradient declarations=${hasGradient ? 'found' : 'none'}`);
  record('banned blue primary absent', !hasBluePrimary, `blue primary pattern=${hasBluePrimary}`);
  record('banned serif hero absent', !hasSerif, `serif font declaration=${hasSerif}`);
  record('banned numbered stepper absent', !hasStepper, `stepper markup=${hasStepper}`);
  const total = lines.filter(line => line.startsWith('PASS |') || line.startsWith('FAIL |')).length;
  lines.push('', `TOTAL: ${total - failures}/${total} checks passed; failures=${failures}.`, 'Scope: static HTML, local assets, and deterministic UI interactions only. No product API or AI path was exercised.');
} finally {
  await browser.close();
}
const output = lines.join(String.fromCharCode(10)) + String.fromCharCode(10);
await fs.writeFile(path.join(root,'CHECK-RESULTS.txt'),output,'utf8');
process.stdout.write(output);
if (failures) process.exitCode = 1;
