import { afterEach, describe, expect, it } from "vitest";
import { locatePassage, locatePassageText, measurePassageGeometry, type PassageFragment, type TextItemBounds } from "./passageLocation";

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

function bounds(
  left: number,
  top: number,
  right: number,
  bottom: number,
  options: Partial<Pick<TextItemBounds, "axisX" | "axisY" | "fontHeight" | "spaceAdvance" | "lineBreakAfter" | "direction">> = {},
): TextItemBounds {
  return {
    left, top, right, bottom,
    direction: options.direction ?? "ltr",
    axisX: options.axisX ?? 1,
    axisY: options.axisY ?? 0,
    fontHeight: options.fontHeight ?? 10,
    spaceAdvance: options.spaceAdvance ?? 5,
    lineBreakAfter: options.lineBreakAfter ?? false,
  };
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
      {
        textItemBounds: [
          bounds(10, 10, 230, 20, { lineBreakAfter: true }),
          bounds(10, 25.5, 230, 35.5, { lineBreakAfter: true }),
          bounds(10, 41, 230, 51),
        ],
      },
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
      bounds(10, 20, 34, 30),
      bounds(34, 20, 92, 30),
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
      bounds(10, 20, 34, 30),
      bounds(90, 20, 148, 30),
    ]);
    const separateLine = locatePassageText(separatedItems, "deposition", [
      bounds(10, 20, 34, 30, { lineBreakAfter: true }),
      bounds(10, 50, 68, 60),
    ]);

    expect(separateColumn.status).toBe("unresolved");
    expect(separateLine.status).toBe("unresolved");
  });

  it("normalizes whitespace and typographic apostrophes conservatively", () => {
    expect(locate("Mars’ early\n history", ["Mars'", "early   history"], {
      textItemBounds: [bounds(10, 20, 35, 30), bounds(40, 20, 100, 30)],
    }).status).toBe("matched");
  });

  it("requires inserted spaces to follow actual same-line text geometry", () => {
    const separateColumns = locatePassageText(["left source phrase", "right column claim"], "left source phrase right column claim", [
      bounds(10, 20, 110, 30),
      bounds(220, 20, 330, 30),
    ]);
    const distantExtractionNeighbors = locatePassageText(["source phrase", "unrelated continuation"], "source phrase unrelated continuation", [
      bounds(10, 20, 105, 30),
      bounds(10, 80, 150, 90),
    ]);

    expect(separateColumns).toEqual({ status: "unresolved", reason: "discontinuous" });
    expect(distantExtractionNeighbors).toEqual({ status: "unresolved", reason: "discontinuous" });
  });

  it("accepts a line-wrapped passage only when PDF.js marks adjacent lines in the same reading column", () => {
    const items = ["legitimate passage continues", "", "on the next line exactly"];
    const matching = locatePassageText(items, "legitimate passage continues on the next line exactly", [
      bounds(20, 20, 180, 30, { lineBreakAfter: true }),
      null,
      bounds(20, 35.5, 160, 45.5),
    ]);
    const otherColumn = locatePassageText(items, "legitimate passage continues on the next line exactly", [
      bounds(20, 20, 180, 30, { lineBreakAfter: true }),
      null,
      bounds(240, 35.5, 380, 45.5),
    ]);

    expect(matching.status).toBe("matched");
    expect(otherColumn).toEqual({ status: "unresolved", reason: "discontinuous" });
  });

  it("keeps a cited range limited to its source phrase when unrelated words share the text item", () => {
    const source = "Verified source passage. Unrelated footer text.";
    const result = locatePassageText([source], "Verified source passage.");

    expect(result).toEqual({
      status: "matched",
      fragments: [{ itemIndex: 0, startOffset: 0, endOffset: "Verified source passage.".length }],
    });
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
