# M9-A-R3 · Conversation-first static proof

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

- Select a suggested question to fill the composer. The static send action explicitly reports **Question not sent.** No generation request occurs.
- Use **Add PDF** to select a local PDF. The selected name appears; there is no upload, page parsing, or indexing.
- Hover or keyboard-focus a citation to see a page thumbnail and a short source quotation. Click it to open the page with the matching verified Poppler highlight. Escape or **Close PDF** closes the sheet and restores focus.
- The page sheet remembers an explicit open/closed preference in per-document `localStorage`. A fresh browser context starts closed. This is a prototype-only preference, not production persistence.
- **Show matches** adds the documented representative source passages and method labels to the page. Turning it off retains only the selected citation mark; it does not change the answer text. The method labels are not outputs of a production retrieval run.
- The reading-progress state shows a page-three image and a contact strip. The strip is illustrative navigation context, not a complete PDF page-navigation implementation.

The mobile sheet is a fixed bottom panel. Opening it scrolls the conversation so the selected sentence and its nearby figure stay above the composer; the page remains independently scrollable inside the sheet. This proves a visual arrangement only, not PDF.js loading, selectable text, or production coordinate conversion.

## Font and mark studies

Open `font-comparison.html` and `logo-comparison.html` on the same loopback server. The chosen proof fonts are Newsreader for answers, Instrument Sans for interface text, and IBM Plex Mono for compact metadata. `FONT-LICENSES.md` records the bundled files, official source revisions, licences, and hashes. Three original 24px-grid mark directions are in `assets/marks/`; their comparison sheet shows 16/32/64px monochrome and neutral-plus-lime samples on dark and paper surfaces, plus header lockups. These are candidates only.

Alternative product names for later discussion: **Folio** (a compact reference to a collection of pages), **Margin** (a place for notes and citations), and **Pagewise** (a plain reference to working through a document). Name availability and trademark clearance are **unchecked**.

## Evidence

`shots/` contains the 18 required full-viewport state screenshots at 1440×900, 1024×768, and 390×844, plus citation popovers, the model-unavailable page-open state, attachment selection, font comparison, and mark comparison. `check.mjs` writes its full Playwright measurements to `CHECK-RESULTS.txt`. These checks are synthetic proof-level evidence; they do not qualify production chat or retrieval.

## Review notes

The most distinctive choices are the warm near-black room, the optional physical-looking paper sheet, and the lime passage mark. My remaining concern is the intentionally empty first-run canvas at wide desktop widths: its restraint reads clearly, but some users may initially want more of the sample page visible. Showing it by default would contradict the approved optional-viewer behavior, so this proof keeps it closed.
