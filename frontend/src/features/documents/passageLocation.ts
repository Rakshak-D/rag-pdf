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
  return Boolean(bounds && [bounds.left, bounds.top, bounds.right, bounds.bottom].every(Number.isFinite) &&
    bounds.right > bounds.left && bounds.bottom > bounds.top);
}

function sameRenderedWordRun(left: TextItemBounds, right: TextItemBounds): boolean {
  if (left.direction !== right.direction || (left.direction !== "ltr" && left.direction !== "rtl")) return false;

  // PDF.js positions adjacent text runs from their rendered advance boxes. A
  // word may cross items only when those boxes share a baseline and their
  // writing-direction edges touch. The epsilon covers floating-point
  // roundoff only; it is not a visual-distance threshold.
  const coordinates = [left.left, left.right, left.bottom, right.left, right.right, right.bottom];
  const epsilon = Math.max(1, ...coordinates.map(Math.abs)) * Number.EPSILON * 64;
  const edgeGap = left.direction === "ltr" ? right.left - left.right : left.left - right.right;
  return Math.abs(edgeGap) <= epsilon && Math.abs(left.bottom - right.bottom) <= epsilon;
}

function hasVerifiedWordContinuity(
  fragments: readonly PassageFragment[],
  textItems: readonly string[],
  textItemBounds: readonly (TextItemBounds | null | undefined)[] | undefined,
): boolean {
  for (let index = 1; index < fragments.length; index += 1) {
    const previous = fragments[index - 1]!;
    const current = fragments[index]!;
    if (previous.itemIndex === current.itemIndex) continue;

    const previousText = textItems[previous.itemIndex]?.slice(previous.startOffset, previous.endOffset) ?? "";
    const currentText = textItems[current.itemIndex]?.slice(current.startOffset, current.endOffset) ?? "";
    if (/\s$/u.test(previousText) || /^\s/u.test(currentText)) continue;
    if (current.itemIndex !== previous.itemIndex + 1) return false;

    const previousBounds = textItemBounds?.[previous.itemIndex];
    const currentBounds = textItemBounds?.[current.itemIndex];
    if (!finiteBounds(previousBounds) || !finiteBounds(currentBounds) ||
      !sameRenderedWordRun(previousBounds, currentBounds)) return false;
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
    const verified = hasVerifiedWordContinuity(candidate, textItems, textItemBounds);
    if (!verified) discontinuousCandidate = true;
    return verified;
  });
  const candidates = [
    ...verifiedNoSeparatorCandidates,
    ...findOccurrences(textItems, passage, true),
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
