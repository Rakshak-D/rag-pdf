export interface PassageFragment {
  itemIndex: number;
  startOffset: number;
  endOffset: number;
}

export type PassageLocationResult =
  | { status: "matched"; fragments: PassageFragment[] }
  | { status: "unresolved"; reason: "invalid-identity" | "document-mismatch" | "manifest-mismatch" | "invalid-page" | "page-mismatch" | "empty-passage" | "not-found" | "ambiguous" };
export type PassageTextMatchResult =
  | { status: "matched"; fragments: PassageFragment[] }
  | { status: "unresolved"; reason: "empty-passage" | "not-found" | "ambiguous" };

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

/** Match only source text; callers must separately establish the document identity. */
export function locatePassageText(textItems: readonly string[], passage: string): PassageTextMatchResult {
  const needle = normalizedPassage(passage);
  if (!needle) return { status: "unresolved", reason: "empty-passage" };
  const candidates = [
    ...findOccurrences(textItems, passage, false),
    ...findOccurrences(textItems, passage, true),
  ];
  const unique = new Map(candidates.map((candidate) => [fragmentKey(candidate), candidate]));
  if (!unique.size) return { status: "unresolved", reason: "not-found" };
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
  return locatePassageText(input.textItems, input.passage);
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
  return ranges.flatMap((range) => Array.from(range.getClientRects()).map((rect) => new DOMRect(
    rect.left - pageRect.left,
    rect.top - pageRect.top,
    rect.width,
    rect.height,
  ))).filter((rect) => rect.width > 0 && rect.height > 0 &&
    [rect.left, rect.top, rect.width, rect.height].every(Number.isFinite));
}
