import { describe, expect, it } from "vitest";
import { locatePassage } from "./passageLocation";

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
    expect(locate("deposition of water", ["depo", "sition of water"])).toEqual({
      status: "matched",
      fragments: [
        { itemIndex: 0, startOffset: 0, endOffset: 4 },
        { itemIndex: 1, startOffset: 0, endOffset: 15 },
      ],
    });
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
