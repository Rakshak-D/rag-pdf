# M9-A-R3-F1 · Conversation-first static proof

This proof is an isolated, scripted composition. It is not the RAG-PDF application and does not call the backend, Ollama, Chroma, or a retrieval service. Its answer, passage marks, method labels, and unavailable-model state are deterministic visual states. The page images, figure, passage text, and Poppler boxes come from the credited NASA factsheet.

## View locally

From the repository root, serve only this directory on loopback:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory docs/design/m9-a-proof
```

Open `http://127.0.0.1:8765/proof-static-not-functional.html?state=first-run`.

The supported state names are `first-run`, `answered-closed`, `answered-open`, `answered-matches`, `model-missing`, and `reading-progress`. The PDF sheet is closed by default in a fresh context. `?state=model-missing&open=1` is the additional composition with the sample page open.

## Before and after

- **Before:** the previous R2 composition boxed too many parts of the answer and split the conversation and PDF into competing panes. **After:** the conversation uses a centered 720px column, the assistant answer is unboxed, and the optional PDF sheet is absent until a citation is selected.
- **Before:** interface text was small and several strings read like product slogans. **After:** answers use 18px Newsreader with 1.66 line-height (17px on mobile), controls use Instrument Sans, and the first screen begins with the sample name, three short questions, and the composer.
- **Before:** the header depended on a logo sketch that reviewers found rough. **After:** the main composition uses only the RAG-PDF wordmark; three mark candidates are compared separately, with no mark selected as the product identity.
- **Before:** opening the PDF and adding a file appeared to be required steps. **After:** the first-run composer works with the sample; the plus control independently selects an optional PDF, and a citation independently opens the PDF sheet. File selection only changes the local visual chip; it does not upload or ingest the file.
- **Before:** citations drew connector lines across conversation and page. **After:** numbered inline citation chips, a short quotation popover, page number, and a one-time passage highlight identify the source without connectors.
- **Before:** source/method information appeared as separate explanation panels. **After:** representative passages and the simulated `words`, `meaning`, and `figure` labels appear only on the page when **Show matches** is turned on.

## Interactions and limits

- Select a suggested question to fill the composer. Submitting the exact first suggestion, **What does Link show?**, replaces the empty thread with the existing pre-authored NASA answer, figure, and three citations. This deterministic transition makes no backend, retrieval, or generation request. The other two suggestions remain source-supported prompts; submitting either reports **No sample answer is scripted for this question.**
- Use **Add PDF** to select a local PDF. The selected name appears; there is no upload, ingestion, parsing, or indexing. The **Open sample pages** action appears only when the selected bytes match the checked-in `mars-science-laboratory.pdf` (1,512,372 bytes; SHA-256 `2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6`). A same-named but different PDF does not expose that action.
- Choosing **Open sample pages** for that verified sample moves to the pre-rendered page-three reading composition and retains the selected filename. This transition is scripted; it does not demonstrate arbitrary-PDF parsing, server work, or a completed index.
- Hover or keyboard-focus a citation to see a page thumbnail and a short source quotation. Click it to open the page with the matching verified Poppler highlight. Escape or **Close PDF** closes the sheet and restores focus.
- The page sheet remembers an explicit open/closed preference in per-document `localStorage`. A fresh browser context starts closed. This is a prototype-only preference, not production persistence.
- **Show matches** adds the documented representative source passages and method labels to the page. Turning it off retains only the selected citation mark; it does not change the answer text. The method labels are not outputs of a production retrieval run.
- The reading-progress state is reached from explicit selection of the verified sample, or opened directly through its query string for capture. It shows page 3 and the contact strip without a percentage, job, indexing claim, or generated result. It is not a complete PDF page-navigation implementation.

The conversation scrolls inside a bounded region above the fixed composer. At 1024×768 the final answer sentence is reachable by scrolling and remains above the composer. The mobile sheet is a fixed bottom panel. Opening a citation magnifies the original page image 2.5× without changing its aspect ratio, scrolls both axes to the selected passage, and keeps the verified overlay marks aligned with that image. **Fit page** returns to the full-page view. These are static Poppler-render interactions only; they do not prove PDF.js loading, selectable text, or production coordinate conversion.

## Font and mark studies

Open `font-comparison.html` and `logo-comparison.html` on the same loopback server. The chosen proof fonts are Newsreader for answers, Instrument Sans for interface text, and IBM Plex Mono for compact metadata. `FONT-LICENSES.md` records the bundled files, official source revisions, licences, and hashes. Three redesigned 24px-grid marks are in `assets/marks/`: an open passage, a reading-current waveform, and a section-sign contour. Their comparison sheet shows 16/32/64px monochrome and neutral-plus-lime samples on dark and paper surfaces, plus header lockups. No candidate is selected. At 16px their silhouettes differ; B can read as a waveform, C deliberately uses a familiar section-sign form, and none uses a file frame, code brackets, or a folded page.

Alternative product names for later discussion: **Folio** (a compact reference to a collection of pages), **Margin** (a place for notes and citations), and **Pagewise** (a plain reference to working through a document). Name availability and trademark clearance are **unchecked**.

## Evidence

`shots/` contains the 18 required full-viewport state screenshots at 1440×900, 1024×768, and 390×844, plus the first-run scripted answer, attachment-to-reading transition, 1024px scrolled answer, all three mobile source passages, citation popovers, the model-unavailable page-open state, font comparison, and mark comparison. `check.mjs` writes its full Playwright measurements to `CHECK-RESULTS.txt`. These checks are synthetic proof-level evidence; they do not qualify production chat or retrieval.

## Review notes

The most distinctive choices are the warm near-black room, the optional physical-looking paper sheet, and the lime passage mark. My remaining concern is the intentionally empty first-run canvas at wide desktop widths: its restraint reads clearly, but some users may initially want more of the sample page visible. Showing it by default would contradict the approved optional-viewer behavior, so this proof keeps it closed.
