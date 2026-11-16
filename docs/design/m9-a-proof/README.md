# M9-A-R4 — Static reading-interface proof

This is a self-contained visual and interaction proof, not the RAG-PDF application. It has no backend connection and does not call Ollama, Chroma, or a retrieval service.

## View the proof locally

From the repository root, serve only this directory on loopback:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory docs/design/m9-a-proof
```

Open `http://127.0.0.1:8765/proof-static-not-functional.html?state=first-run`. Directly addressable states are `first-run`, `answered-closed`, `answered-open`, `answered-matches`, `model-unreachable`, `starting`, `reading-progress`, and `answer-shapes`.

The checked browser was Playwright-managed Chrome channel `157.0.8103.51`. The proof loads bundled fonts, page renders, figures, and icons from this directory; the recorded browser checks saw no requests outside `127.0.0.1:8765`.

## What the static interactions demonstrate

- The first screen opens with the NASA sample identity, three source-supported suggested questions, and the composer. The PDF viewer is closed by default.
- Selecting and sending the first suggested Link question reveals one pre-authored answer fixture, its NASA figure and credit, and three source citations. This transition is local and deterministic; it makes no API, model, or retrieval request.
- The other suggested questions are retained as prompts. A missing answer fixture is stated plainly; no answer is generated for them.
- Citation hover and keyboard focus show a short quoted passage. Selecting a citation opens the original source page render at its verified highlight. On mobile, the source opens at 118% and the page scrolls to the complete highlight. Repeated zoom-out returns to the width-fit composition.
- `Show matches` displays simulated source-method labels and passage marks directly on the page. It fits the page and its dark label gutter together. It does not alter the answer.
- The plus control selects a local PDF. Only the checked-in NASA sample with the expected filename, 1,512,372-byte length, and SHA-256 `2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6` enables **Open sample pages**. The proof does not upload, parse, or index the selected file. Other PDFs remain filename-only.
- The `model-unreachable` and `starting` states are static compositions. Neither checks Ollama or reports a live model state.
- The reading-progress state uses a checked-in page image and contact strip. It is not a job, ingestion, or indexing operation.

## Evidence and limits

The page images are genuine Poppler renders of the four-page NASA Mars Science Laboratory/Curiosity factsheet. The Link image is the genuine extracted NASA/JPL-Caltech/MSSS figure. Passage boxes were verified from the source PDF with Poppler `pdftotext -bbox-layout` and align with these raster page renders. The proof does not demonstrate the production PDF.js coordinate conversion, selectable PDF text, or retrieval provenance from a running index.

The answer, five answer-shape fixtures, passage marks, and method classifications are authored static proof content. The method labels are illustrative only in these proof documents and were not returned by a retrieval run. No model was loaded, no generation was performed, and no backend test was run for this visual proof.

`check.mjs` reports browser geometry, interaction, reduced-motion, contrast, font/network, screenshot, and copy measurements in `CHECK-RESULTS.txt`. Its passing results apply only to this static proof. One exact-copy conflict remains: R4 requires the starting message “Getting the model ready. The first answer can take a minute.” while the same copy restrictions ban “Model ready”; the required sentence contains that phrase and is left unchanged.

See `CREDITS.md` for source and coordinate provenance, `FONT-LICENSES.md` for bundled font licences, `copy.md` for the visible-string inventory, `TASTE.md` for the review, and `R4-CHANGES.md` for the round-over-round changes.
