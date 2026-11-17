# M9-A-R5 — Final static reading-interface proof

This self-contained proof shows the proposed RAG-PDF reading interface. It is not the application and does not connect to a backend, Ollama, Chroma, or a retrieval service.

## View and verify

From the repository root, serve only this directory on loopback:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory docs/design/m9-a-proof
```

Open `http://127.0.0.1:8765/proof-static-not-functional.html?state=first-run`. Direct states are `first-run`, `answered-closed`, `answered-open`, `answered-matches`, `model-unreachable`, `reading-progress`, and `answer-shapes`. The removed `starting` query falls back to the no-status first-run screen.

Run the browser proof from the repository root:

```powershell
node docs/design/m9-a-proof/capture.mjs
node docs/design/m9-a-proof/check.mjs
node --check docs/design/m9-a-proof/capture.mjs
node --check docs/design/m9-a-proof/check.mjs
```

The scripts use the locally installed Playwright-managed Chrome channel and write the nine R5 screenshots plus one logo comparison under `shots/`. Earlier R4 screenshots remain unchanged as historical evidence. See `CHECK-RESULTS.txt` for the browser version, measurements, individual pass/fail results, and limitations.

## What the proof demonstrates

- First run places a genuine page-one render of the four-page NASA factsheet beside three suggested questions and the composer. A lime mark is aligned with the verified words “Gale Crater”; the page preview does not open the optional PDF sheet.
- Selecting and submitting each suggested question produces a deterministic, pre-authored answer fixture. The Link question includes the credited NASA figure, the launch-power answer cites the page-three 110-watt fact, and the crater/drill answer cites the landing site and John Klein sample. No API, model, or retrieval request occurs.
- Citation popovers flow below their cited paragraph, keeping the sentence, user turn, and following content unobscured. Selecting a citation opens the original page render at the source passage.
- Mobile source zoom is computed from the selected passage width and sheet viewport, capped at 200%. The check measures all three sources across 390px-wide 667px, 740px, and 844px viewports at 30%, 55%, and 92% sheet snaps.
- `Show matches` displays simulated method labels and source overlays directly on the PDF page. It is off by default and does not change answer text.
- The unavailable-model state is a static failure composition. “Check again” reports that this proof does not test a live service; PDF viewing remains independently available.
- File selection is optional. Arbitrary PDFs remain filename-only; the proof does not parse or index them. The reading-progress state is a static page-three composition, not a submitted job.

## Evidence and limits

The page renders are genuine Poppler images of the source NASA PDF. The Link figure is a genuine extracted image with its original caption and credit. `CREDITS.md` records source passages, URLs, figure provenance, and coordinate verification. These assets validate only this static raster proof; they do not verify production PDF.js coordinate conversion, indexing, retrieval, or generation.

The answer fixtures and method classifications are authored proof content. No model was loaded, no benchmark was run, and no answer is presented as a live model output. `copy.md` inventories visible interface strings and measured word counts. `TASTE.md` records the final design review. `R5-CHANGES.md` lists the corrective changes and remaining limits. `FONT-LICENSES.md` records local font licences.
