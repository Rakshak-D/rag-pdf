# M9-A-R5 — Corrective changes and locked design

R5 preserves the accepted R4 answered-screen direction. Changes are limited to the static proof under this directory; they do not wire the proof to the product application.

## Eight requested corrections

| Fix | Before | R5 result | Evidence |
|---|---|---|---|
| 1. Citation popovers | Review found the popover covered its cited sentence and the user bubble. | The quotation is placed in document flow after the cited answer paragraph. At 1440×900 and 1024×768, all three Link-answer citations measured 0px² intersection with the cited sentence and user bubble, stayed inside the conversation column and viewport, and intersected no other answer sentence or figure. | `CHECK-RESULTS.txt`; `shots/r5-citation-popover-1440.png`; `shots/r5-citation-popover-1024.png` |
| 2. First-run document hero | First run looked like a generic centered chat. | Desktop now pairs the left-side composer, sample identity and three questions with a genuine page-one render on the right. The page is 430×556.47px at 1440×900, preserving the 612:792 ratio. Mobile places the 231.81×300px page above three stacked questions and the composer. The viewer remains closed and optional. The lime mark matches the verified “Gale Crater” source box. | `CHECK-RESULTS.txt`; `shots/r5-first-run-1440.png`; `shots/r5-first-run-390.png`; `CREDITS.md` |
| 3. Suggested questions | The power suggestion did not have a complete submission path. | All three suggestions fill the composer and submit a deterministic source-backed fixture without a backend request: Link → page 2; launch generator power → “about 110 watts of electrical power,” page 3; landing crater and first drilled sample → Gale Crater and John Klein, pages 1–2. The source and selected page are checked after citation activation. | `CHECK-RESULTS.txt`; `CREDITS.md` |
| 4. Fixture C | The answer was about 300 words and hedged. | Fixture C is four direct sentences, with citations for Gale Crater, landing sequence, 110-watt generator output and the John Klein sample. Fixture E's insufficient-evidence text and citation were verified unchanged. | `CHECK-RESULTS.txt`; `shots/r5-answer-shapes.png`; `CREDITS.md` |
| 5. Mobile source visibility | The former fixed 118%/pinned-clearance check did not cover all sheet sizes or viewport heights. | The new test intersects the viewport, PDF sheet, page viewport, clipping ancestors and floating toolbar. All 27 passage cases (three sources × three heights × three snaps) pass: minimum clearance 9.20px, maximum center deviation 4.53%, zoom 110–115%, and unchanged page ratio about 0.77273. Horizontal and vertical scrolling are recorded. Manual review of the genuine page screenshots confirmed each passage is readable; the smallest highlight-line rectangle is 15.66px high. | `CHECK-RESULTS.txt`; `shots/r5-mobile-sheet-30.png`; `shots/r5-mobile-sheet-55.png`; `shots/r5-mobile-sheet-92.png`; `CREDITS.md` |
| 6. Unavailable-model recovery | “Check again” was not visibly outlined and the message did not state the corrective action. | The exact line is “Can't reach the model. Start Ollama, then check again.” The neutral outlined button reports that this static proof does not check a live service; the text link opens local instructions. The PDF remains independently viewable and no ready state is claimed. | `CHECK-RESULTS.txt`; `shots/r5-model-unreachable-1440.png` |
| 7. Mark studies | Candidate C and the B monochrome study remained in the comparison. | Candidate C's proof-local SVG and B monochrome comparison were removed. Only provisional A and the two-tone wordmark study B remain, shown at 16/32/64px on dark and paper surfaces. No winner is selected. The heading now correctly says “Two mark directions.” At 16px, A still resembles a familiar document glyph; it is retained only as the owner's provisional option, not claimed as uniquely original. | `logo-comparison.html`; `shots/r5-logo-comparison.png`; `CHECK-RESULTS.txt` |
| 8. User-bubble clearance | At 1440px, the bubble was about 13px from the PDF sheet. | The measured nearest-edge gap is 36px, exceeding the 24px requirement; horizontal overflow is false. | `CHECK-RESULTS.txt` |

## Copy and status resolution

The starting-status sentence and all “Model ready” copy are absent. The sample metadata is now `4 pages` (instead of `Sample, 4 pages`), bringing the model-unavailable screen to exactly 25 chrome words without changing the required failure line or controls. The other 1440px states measure 4–22 chrome words. First-run has 10 chrome words plus 32 words in suggested questions; see `copy.md` for the full state table and inventory.

## Verification

- Playwright-managed Chrome channel: `157.0.8103.79`.
- `node --check docs/design/m9-a-proof/capture.mjs`: passed.
- `node --check docs/design/m9-a-proof/check.mjs`: passed.
- `node docs/design/m9-a-proof/capture.mjs`: nine required R5 screenshots and one logo comparison captured at their specified viewports.
- `node docs/design/m9-a-proof/check.mjs`: **99 PASS, 0 FAIL, 3 NOT_TESTED; 102 records.** The three NOT_TESTED records are the two R4 center-composer checks and the R4 fixed-zoom mobile gate, all superseded by R5 requirements and not counted as passes.
- `git diff --check`: passed before staging; staged diff will be checked again before commit.

The proof is static. It does not establish live retrieval, ingestion, generation, Ollama health, arbitrary-PDF parsing, production PDF.js coordinate conversion, or product behavior. The exact source passages and Poppler-coordinate limits are in `CREDITS.md`. The design is locked after R5; remaining work is implementation and functional verification only after separate approval.
