import { afterEach, describe, expect, it } from "vitest";
import { locatePassage, locatePassageText, measurePassageGeometry, type PassageFragment } from "./passageLocation";

const documentId = `doc_${"a".repeat(64)}`;
const manifestHash = "b".repeat(64);

function locate(passage: string, textItems: readonly string[], overrides: Partial<Parameters<typeof locatePassage>[0]> = {}) {
  return locatePassage({
    loadedDocumentId: documentId,
    citedDocumentId: documentId,
    loadedManifestHash: manifestHash,
    citedManifestHash: manifestHash,
    pageNumber: 2,
    loadedPageNumber: 2,
    pageCount: 4,
    passage,
    textItems,
    ...overrides,
  });
}

describe("locatePassage", () => {
  it("matches text fragmented across rendered PDF.js items and ignores surrounding line text", () => {
    const result = locate(
      "The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.",
      [
        "The evidence for stream flow was in rounded",
        "pebbles mixed with hardened sand in conglomerate rocks at and",
        "near the landing site. Analysis of Mars' atmospheric composition",
      ],
    );

    expect(result).toEqual({
      status: "matched",
      fragments: [
        { itemIndex: 0, startOffset: 0, endOffset: 43 },
        { itemIndex: 1, startOffset: 0, endOffset: 61 },
        { itemIndex: 2, startOffset: 0, endOffset: 22 },
      ],
    });
  });

  it("handles a word split across PDF.js text items without inserting a guessed space", () => {
    expect(locatePassageText(["depo", "sition of water"], "deposition of water", [
      { left: 10, top: 20, right: 34, bottom: 30, direction: "ltr" },
      { left: 34, top: 20, right: 92, bottom: 30, direction: "ltr" },
    ])).toEqual({
      status: "matched",
      fragments: [
        { itemIndex: 0, startOffset: 0, endOffset: 4 },
        { itemIndex: 1, startOffset: 0, endOffset: 15 },
      ],
    });
  });

  it("does not join split-word text items without verified visual continuity", () => {
    expect(locatePassageText(["depo", "sition"], "deposition").status).toBe("unresolved");
  });

  it("rejects split-word items on another line or distant in the same line", () => {
    const separatedItems = ["depo", "sition"];
    const separateColumn = locatePassageText(separatedItems, "deposition", [
      { left: 10, top: 20, right: 34, bottom: 30, direction: "ltr" },
      { left: 90, top: 20, right: 148, bottom: 30, direction: "ltr" },
    ]);
    const separateLine = locatePassageText(separatedItems, "deposition", [
      { left: 10, top: 20, right: 34, bottom: 30, direction: "ltr" },
      { left: 10, top: 50, right: 68, bottom: 60, direction: "ltr" },
    ]);

    expect(separateColumn.status).toBe("unresolved");
    expect(separateLine.status).toBe("unresolved");
  });

  it("normalizes whitespace and typographic apostrophes conservatively", () => {
    expect(locate("Mars’ early\n history", ["Mars'", "early   history"]).status).toBe("matched");
  });

  it("rejects a repeated passage instead of selecting the first occurrence", () => {
    expect(locate("same phrase", ["same phrase", "other", "same phrase"])).toEqual({
      status: "unresolved",
      reason: "ambiguous",
    });
  });

  it("fails closed for missing text, identity, manifest and page mismatches", () => {
    expect(locate("not present", ["different source text"])).toEqual({ status: "unresolved", reason: "not-found" });
    expect(locate("source", ["source"], { citedDocumentId: "doc-invalid" })).toEqual({ status: "unresolved", reason: "invalid-identity" });
    expect(locate("source", ["source"], { citedDocumentId: `doc_${"c".repeat(64)}` })).toEqual({ status: "unresolved", reason: "document-mismatch" });
    expect(locate("source", ["source"], { citedManifestHash: "d".repeat(64) })).toEqual({ status: "unresolved", reason: "manifest-mismatch" });
    expect(locate("source", ["source"], { loadedPageNumber: 3 })).toEqual({ status: "unresolved", reason: "page-mismatch" });
    expect(locate("source", ["source"], { pageNumber: 0 })).toEqual({ status: "unresolved", reason: "invalid-page" });
    expect(locate("source", ["source"], { pageCount: 1 })).toEqual({ status: "unresolved", reason: "invalid-page" });
  });
});

describe("measurePassageGeometry", () => {
  const fragments: PassageFragment[] = [
    { itemIndex: 0, startOffset: 0, endOffset: 5 },
    { itemIndex: 1, startOffset: 0, endOffset: 6 },
  ];
  const textItems = ["first", "second"];
  const pageRect = { left: 0, top: 0, width: 100, height: 100 };

  function textDivs(): HTMLElement[] {
    return textItems.map((text) => {
      const element = document.createElement("span");
      element.textContent = text;
      document.body.append(element);
      return element;
    });
  }

  function measureFor(rects: readonly DOMRect[]) {
    return (
      element: HTMLElement,
      _fragment: Pick<PassageFragment, "startOffset" | "endOffset">,
    ) => {
      const index = textItems.indexOf(element.textContent ?? "");
      const rect = rects[index];
      return rect ? [rect] : [];
    };
  }

  afterEach(() => document.body.replaceChildren());

  it("rejects one missing rendered fragment in an otherwise valid multiline passage", () => {
    const divs = textDivs();
    expect(measurePassageGeometry(fragments, textItems, [divs[0], undefined], pageRect, measureFor([
      new DOMRect(10, 10, 20, 8), new DOMRect(10, 20, 24, 8),
    ]))).toEqual({ status: "unresolved", reason: "geometry-unavailable" });
  });

  it("rejects empty geometry for any fragment", () => {
    expect(measurePassageGeometry(fragments, textItems, textDivs(), pageRect, measureFor([
      new DOMRect(10, 10, 20, 8),
    ]))).toEqual({ status: "unresolved", reason: "geometry-unavailable" });
  });

  it("rejects non-finite, empty or out-of-page geometry without retaining earlier marks", () => {
    expect(measurePassageGeometry(fragments, textItems, textDivs(), pageRect, measureFor([
      new DOMRect(10, 10, 20, 8), new DOMRect(Number.NaN, 20, 24, 8),
    ]))).toEqual({ status: "unresolved", reason: "geometry-unavailable" });
    expect(measurePassageGeometry(fragments, textItems, textDivs(), pageRect, measureFor([
      new DOMRect(10, 10, 20, 8), new DOMRect(10, 20, 0, 8),
    ]))).toEqual({ status: "unresolved", reason: "geometry-unavailable" });
    expect(measurePassageGeometry(fragments, textItems, textDivs(), pageRect, measureFor([
      new DOMRect(10, 10, 20, 8), new DOMRect(95, 20, 10, 8),
    ]))).toEqual({ status: "unresolved", reason: "geometry-unavailable" });
  });

  it("returns marks only when every fragment has complete in-page geometry", () => {
    const result = measurePassageGeometry(fragments, textItems, textDivs(), pageRect, measureFor([
      new DOMRect(10, 10, 20, 8), new DOMRect(10, 20, 24, 8),
    ]));

    expect(result).toEqual({
      status: "matched",
      marks: [
        { itemIndex: 0, left: 10, top: 10, width: 20, height: 8 },
        { itemIndex: 1, left: 10, top: 20, width: 24, height: 8 },
      ],
    });
  });
});
