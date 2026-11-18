export interface PassageFragment {
  itemIndex: number;
  startOffset: number;
  endOffset: number;
}

export interface TextItemBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
  direction: "ltr" | "rtl" | "ttb";
  /** Unit vector in PDF.js reading order, derived from the rendered text span transform. */
  axisX: number;
  axisY: number;
  /** Rendered font size and one measured space advance, in CSS pixels. */
  fontHeight: number;
  spaceAdvance: number;
  /** PDF.js marks an explicit line break after this text item. */
  lineBreakAfter: boolean;
}

export interface PageRelativeRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export type PassageEvidenceMark = PageRelativeRect & { itemIndex: number };
export type PassageGeometryResult =
  | { status: "matched"; marks: PassageEvidenceMark[] }
  | { status: "unresolved"; reason: "geometry-unavailable" };

export type PassageLocationResult =
  | { status: "matched"; fragments: PassageFragment[] }
  | { status: "unresolved"; reason: "invalid-identity" | "document-mismatch" | "manifest-mismatch" | "invalid-page" | "page-mismatch" | "empty-passage" | "not-found" | "ambiguous" | "discontinuous" };
export type PassageTextMatchResult =
  | { status: "matched"; fragments: PassageFragment[] }
  | { status: "unresolved"; reason: "empty-passage" | "not-found" | "ambiguous" | "discontinuous" };

export interface PassageLocationInput {
  loadedDocumentId: string;
  citedDocumentId: string;
  loadedManifestHash: string;
  citedManifestHash: string;
  pageNumber: number;
  loadedPageNumber: number;
  pageCount: number;
  passage: string;
  textItems: readonly string[];
  textItemBounds?: readonly (TextItemBounds | null | undefined)[];
}

interface SourcePosition {
  itemIndex: number;
  startOffset: number;
  endOffset: number;
}

interface NormalizedText {
  value: string;
  positions: Array<SourcePosition | null>;
}

const DOCUMENT_ID = /^doc_[0-9a-f]{64}$/;
const HASH = /^[0-9a-f]{64}$/;

function normalizeCodePoint(value: string): string {
  return value.normalize("NFKC")
    .replace(/[\u2018\u2019\u02bc]/g, "'")
    .replace(/[\u2010-\u2015\u2212]/g, "-");
}

function appendNormalized(
  target: NormalizedText,
  value: string,
  position: SourcePosition | null,
): void {
  const normalized = normalizeCodePoint(value);
  for (const character of normalized) {
    if (/\s/u.test(character)) {
      if (!target.value || target.value.endsWith(" ")) continue;
      target.value += " ";
      target.positions.push(position);
      continue;
    }
    target.value += character;
    for (let index = 0; index < character.length; index += 1) target.positions.push(position);
  }
}

function normalizedDocument(items: readonly string[], addItemSeparators: boolean): NormalizedText {
  const result: NormalizedText = { value: "", positions: [] };
  items.forEach((item, itemIndex) => {
    if (addItemSeparators && itemIndex > 0) {
      appendNormalized(result, " ", null);
    }
    for (let offset = 0; offset < item.length;) {
      const codePoint = item.codePointAt(offset);
      if (codePoint === undefined) break;
      const value = String.fromCodePoint(codePoint);
      appendNormalized(result, value, {
        itemIndex,
        startOffset: offset,
        endOffset: offset + value.length,
      });
      offset += value.length;
    }
  });
  return result;
}

function normalizedPassage(value: string): string {
  const result: NormalizedText = { value: "", positions: [] };
  for (let offset = 0; offset < value.length;) {
    const codePoint = value.codePointAt(offset);
    if (codePoint === undefined) break;
    const character = String.fromCodePoint(codePoint);
    appendNormalized(result, character, null);
    offset += character.length;
  }
  return result.value.trim();
}

function fragmentsAt(text: NormalizedText, start: number, end: number): PassageFragment[] {
  const fragments: PassageFragment[] = [];
  for (let index = start; index < end; index += 1) {
    const position = text.positions[index];
    if (!position) continue;
    const previous = fragments.at(-1);
    if (previous && previous.itemIndex === position.itemIndex && position.startOffset <= previous.endOffset) {
      previous.endOffset = Math.max(previous.endOffset, position.endOffset);
    } else {
      fragments.push({ ...position });
    }
  }
  return fragments;
}

function findOccurrences(items: readonly string[], passage: string, addItemSeparators: boolean): PassageFragment[][] {
  const haystack = normalizedDocument(items, addItemSeparators);
  const needle = normalizedPassage(passage);
  if (!needle) return [];
  const occurrences: PassageFragment[][] = [];
  let from = 0;
  while (from <= haystack.value.length - needle.length) {
    const index = haystack.value.indexOf(needle, from);
    if (index < 0) break;
    const fragments = fragmentsAt(haystack, index, index + needle.length);
    if (fragments.length) occurrences.push(fragments);
    from = index + 1;
  }
  return occurrences;
}

function fragmentKey(fragments: readonly PassageFragment[]): string {
  return fragments.map(({ itemIndex, startOffset, endOffset }) => `${itemIndex}:${startOffset}:${endOffset}`).join("|");
}

function finiteBounds(bounds: TextItemBounds | null | undefined): bounds is TextItemBounds {
  return Boolean(bounds && [bounds.left, bounds.top, bounds.right, bounds.bottom,
    bounds.axisX, bounds.axisY, bounds.fontHeight, bounds.spaceAdvance].every(Number.isFinite) &&
    bounds.right > bounds.left && bounds.bottom > bounds.top && bounds.fontHeight > 0 && bounds.spaceAdvance > 0 &&
    typeof bounds.lineBreakAfter === "boolean" &&
    Math.abs(Math.hypot(bounds.axisX, bounds.axisY) - 1) < Number.EPSILON * 128);
}

interface ProjectedTextRun {
  axisX: number;
  axisY: number;
  start: number;
  end: number;
  baseline: number;
}

function projectedTextRun(bounds: TextItemBounds): ProjectedTextRun | null {
  const axisX = bounds.axisX;
  const axisY = bounds.axisY;
  const epsilon = Number.EPSILON * 128;
  // The DOMRect is an axis-aligned box. Its projection is exact for horizontal
  // and vertical PDF.js text runs (including 90-degree rotated pages). For an
  // oblique run, reject cross-item stitching rather than overestimate its edges.
  if (Math.abs(axisX) > epsilon && Math.abs(axisY) > epsilon) return null;

  const centerX = (bounds.left + bounds.right) / 2;
  const centerY = (bounds.top + bounds.bottom) / 2;
  const extent = Math.abs(axisX) * (bounds.right - bounds.left) + Math.abs(axisY) * (bounds.bottom - bounds.top);
  const centerAlong = centerX * axisX + centerY * axisY;
  const centerAcross = centerX * -axisY + centerY * axisX;
  return {
    axisX,
    axisY,
    start: centerAlong - extent / 2,
    end: centerAlong + extent / 2,
    baseline: centerAcross,
  };
}

function continuityEpsilon(...values: number[]): number {
  return Math.max(1, ...values.map(Math.abs)) * Number.EPSILON * 128;
}

function sameReadingAxis(left: ProjectedTextRun, right: ProjectedTextRun): boolean {
  const cross = left.axisX * right.axisY - left.axisY * right.axisX;
  const dot = left.axisX * right.axisX + left.axisY * right.axisY;
  return Math.abs(cross) <= Number.EPSILON * 128 && dot > 0;
}

function sameRenderedLine(left: TextItemBounds, right: TextItemBounds, epsilon: number): boolean {
  const leftRun = projectedTextRun(left);
  const rightRun = projectedTextRun(right);
  return Boolean(leftRun && rightRun && sameReadingAxis(leftRun, rightRun) &&
    Math.abs(leftRun.baseline - rightRun.baseline) <= epsilon);
}

function lineContinuation(left: TextItemBounds, right: TextItemBounds, epsilon: number): boolean {
  const leftRun = projectedTextRun(left);
  const rightRun = projectedTextRun(right);
  if (!leftRun || !rightRun || !sameReadingAxis(leftRun, rightRun)) return false;

  // A line transition is accepted only when PDF.js supplies an EOL marker, the
  // next line begins at the same column origin, and the baseline advance is no
  // more than two rendered font ems. This rejects gaps/column jumps without a
  // fixed-pixel distance threshold. Split words still require exact same-line
  // edge contact below.
  const advance = Math.abs(rightRun.baseline - leftRun.baseline);
  return left.lineBreakAfter && Math.abs(rightRun.start - leftRun.start) <= epsilon &&
    advance > epsilon && advance <= 2 * Math.max(left.fontHeight, right.fontHeight);
}

function hasVerifiedTextContinuity(
  fragments: readonly PassageFragment[],
  textItems: readonly string[],
  textItemBounds: readonly (TextItemBounds | null | undefined)[] | undefined,
  allowLineBreaks: boolean,
): boolean {
  for (let index = 1; index < fragments.length; index += 1) {
    const previous = fragments[index - 1]!;
    const current = fragments[index]!;
    if (previous.itemIndex === current.itemIndex) continue;

    if (current.itemIndex <= previous.itemIndex) return false;
    for (let skipped = previous.itemIndex + 1; skipped < current.itemIndex; skipped += 1) {
      if (textItems[skipped] !== "") return false;
    }

    const previousBounds = textItemBounds?.[previous.itemIndex];
    const currentBounds = textItemBounds?.[current.itemIndex];
    if (!finiteBounds(previousBounds) || !finiteBounds(currentBounds)) return false;
    const epsilon = continuityEpsilon(previousBounds.left, previousBounds.top, previousBounds.right, previousBounds.bottom,
      currentBounds.left, currentBounds.top, currentBounds.right, currentBounds.bottom);

    if (sameRenderedLine(previousBounds, currentBounds, epsilon)) {
      const previousRun = projectedTextRun(previousBounds);
      const currentRun = projectedTextRun(currentBounds);
      if (!previousRun || !currentRun) return false;
      const gap = currentRun.start - previousRun.end;
      const previousText = textItems[previous.itemIndex]?.slice(previous.startOffset, previous.endOffset) ?? "";
      const currentText = textItems[current.itemIndex]?.slice(current.startOffset, current.endOffset) ?? "";
      const splitWord = !allowLineBreaks && !/\s$/u.test(previousText) && !/^\s/u.test(currentText);
      if (splitWord ? Math.abs(gap) > epsilon : gap < -epsilon || gap > Math.max(previousBounds.spaceAdvance, currentBounds.spaceAdvance) + epsilon) {
        return false;
      }
      continue;
    }

    if (!allowLineBreaks || !lineContinuation(previousBounds, currentBounds, epsilon)) return false;

    const previousText = textItems[previous.itemIndex]?.slice(previous.startOffset, previous.endOffset) ?? "";
    const currentText = textItems[current.itemIndex]?.slice(current.startOffset, current.endOffset) ?? "";
    if (!allowLineBreaks && !(/\s$/u.test(previousText) || /^\s/u.test(currentText))) return false;
  }
  return true;
}

/** Match only source text; callers must separately establish the document identity. */
export function locatePassageText(
  textItems: readonly string[],
  passage: string,
  textItemBounds?: readonly (TextItemBounds | null | undefined)[],
): PassageTextMatchResult {
  const needle = normalizedPassage(passage);
  if (!needle) return { status: "unresolved", reason: "empty-passage" };
  const noSeparatorCandidates = findOccurrences(textItems, passage, false);
  let discontinuousCandidate = false;
  const verifiedNoSeparatorCandidates = noSeparatorCandidates.filter((candidate) => {
    const verified = hasVerifiedTextContinuity(candidate, textItems, textItemBounds, false);
    if (!verified) discontinuousCandidate = true;
    return verified;
  });
  const separatedCandidates = findOccurrences(textItems, passage, true).filter((candidate) => {
    const verified = hasVerifiedTextContinuity(candidate, textItems, textItemBounds, true);
    if (!verified) discontinuousCandidate = true;
    return verified;
  });
  const candidates = [
    ...verifiedNoSeparatorCandidates,
    ...separatedCandidates,
  ];
  const unique = new Map(candidates.map((candidate) => [fragmentKey(candidate), candidate]));
  if (!unique.size) return { status: "unresolved", reason: discontinuousCandidate ? "discontinuous" : "not-found" };
  if (unique.size !== 1) return { status: "unresolved", reason: "ambiguous" };
  return { status: "matched", fragments: [...unique.values()][0]! };
}

/**
 * Locate a citation only when its document, manifest, page and full passage all
 * agree with the currently loaded PDF.js document. It deliberately fails
 * closed when text is missing or has more than one plausible occurrence.
 */
export function locatePassage(input: PassageLocationInput): PassageLocationResult {
  if (!DOCUMENT_ID.test(input.loadedDocumentId) || !DOCUMENT_ID.test(input.citedDocumentId) ||
    !HASH.test(input.loadedManifestHash) || !HASH.test(input.citedManifestHash)) {
    return { status: "unresolved", reason: "invalid-identity" };
  }
  if (input.loadedDocumentId !== input.citedDocumentId) return { status: "unresolved", reason: "document-mismatch" };
  if (input.loadedManifestHash !== input.citedManifestHash) return { status: "unresolved", reason: "manifest-mismatch" };
  if (!Number.isSafeInteger(input.pageNumber) || !Number.isSafeInteger(input.loadedPageNumber) ||
    !Number.isSafeInteger(input.pageCount) || input.pageCount < 1 || input.pageNumber < 1 || input.pageNumber > input.pageCount) {
    return { status: "unresolved", reason: "invalid-page" };
  }
  if (input.pageNumber !== input.loadedPageNumber) return { status: "unresolved", reason: "page-mismatch" };
  return locatePassageText(input.textItems, input.passage, input.textItemBounds);
}

function textNodes(element: HTMLElement): Array<{ node: Text; start: number; end: number }> {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const nodes: Array<{ node: Text; start: number; end: number }> = [];
  let offset = 0;
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    const end = offset + node.data.length;
    nodes.push({ node, start: offset, end });
    offset = end;
  }
  return nodes;
}

/** Measure a matched source fragment from the PDF.js text layer in CSS pixels. */
export function measureTextFragment(
  textDiv: HTMLElement,
  fragment: Pick<PassageFragment, "startOffset" | "endOffset">,
  pageRect: Pick<DOMRect, "left" | "top">,
): DOMRect[] {
  const nodes = textNodes(textDiv);
  const ranges: Range[] = [];
  for (const entry of nodes) {
    const start = Math.max(fragment.startOffset, entry.start);
    const end = Math.min(fragment.endOffset, entry.end);
    if (start >= end) continue;
    const range = document.createRange();
    range.setStart(entry.node, start - entry.start);
    range.setEnd(entry.node, end - entry.start);
    ranges.push(range);
  }
  const measuredRanges = ranges.map((range) => Array.from(range.getClientRects()).map((rect) => new DOMRect(
    rect.left - pageRect.left,
    rect.top - pageRect.top,
    rect.width,
    rect.height,
  )));
  if (measuredRanges.some((rects) => rects.length === 0)) return [];
  return measuredRanges.flat();
}

/** Return geometry only when every matched fragment is present and measurable. */
export function measurePassageGeometry(
  fragments: readonly PassageFragment[],
  textItems: readonly string[],
  textDivs: readonly (HTMLElement | null | undefined)[],
  pageRect: Pick<DOMRect, "left" | "top" | "width" | "height">,
  measure: (
    textDiv: HTMLElement,
    fragment: Pick<PassageFragment, "startOffset" | "endOffset">,
    pageRect: Pick<DOMRect, "left" | "top">,
  ) => DOMRect[] = measureTextFragment,
): PassageGeometryResult {
  if (!fragments.length || ![pageRect.left, pageRect.top, pageRect.width, pageRect.height].every(Number.isFinite) ||
    pageRect.width <= 0 || pageRect.height <= 0) {
    return { status: "unresolved", reason: "geometry-unavailable" };
  }

  const marks: PassageEvidenceMark[] = [];
  for (const fragment of fragments) {
    const sourceText = textItems[fragment.itemIndex];
    const textDiv = textDivs[fragment.itemIndex];
    if (!Number.isSafeInteger(fragment.itemIndex) || fragment.itemIndex < 0 ||
      !Number.isSafeInteger(fragment.startOffset) || !Number.isSafeInteger(fragment.endOffset) ||
      fragment.startOffset < 0 || fragment.endOffset <= fragment.startOffset ||
      sourceText === undefined || fragment.endOffset > sourceText.length ||
      !textDiv?.isConnected || textDiv.textContent !== sourceText) {
      return { status: "unresolved", reason: "geometry-unavailable" };
    }

    let rects: DOMRect[];
    try {
      rects = measure(textDiv, fragment, pageRect);
    } catch {
      return { status: "unresolved", reason: "geometry-unavailable" };
    }
    if (!Array.isArray(rects) || !rects.length || rects.some((rect) =>
      ![rect.left, rect.top, rect.width, rect.height].every(Number.isFinite) ||
      rect.width <= 0 || rect.height <= 0 || rect.left < 0 || rect.top < 0 ||
      rect.left + rect.width > pageRect.width || rect.top + rect.height > pageRect.height)) {
      return { status: "unresolved", reason: "geometry-unavailable" };
    }
    marks.push(...rects.map((rect) => ({
      itemIndex: fragment.itemIndex,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    })));
  }
  return { status: "matched", marks };
}
