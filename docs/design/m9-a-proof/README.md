# M9-A Reading Desk static design proof

This directory contains a static, nonfunctional design proof. It is not connected to the RAG-PDF backend, Chroma, Ollama, or any live retrieval service. The answer and evidence layout demonstrates a composition using real NASA document material; it does not claim that a live question was asked or answered.

## View locally

From the repository root, start a loopback-only static server:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory docs/design/m9-a-proof
```

Open one of these local URLs:

- `http://127.0.0.1:8765/proof-static-not-functional.html?state=drop`
- `http://127.0.0.1:8765/proof-static-not-functional.html?state=reading`
- `http://127.0.0.1:8765/proof-static-not-functional.html?state=answered`
- `http://127.0.0.1:8765/proof-static-not-functional.html?state=model-missing`

All document images, fonts, styles and scripts are local to this directory. No application services or models are needed.

In the answered state, each numbered source chip selects a separate verified passage on page 2: the Link caption, the stream-flow paragraph or the John Klein drilling paragraph. On narrow screens, the reader crops around the selected source and routes its leader through the outer gutter. The `drop` state has no sample title, page count or page controls. “Model ready” and “Ollama unavailable” are static composition states, not live service checks.

## Evidence

`shots/` contains full-viewport browser captures at 1440×900, 1024×768 and 390×844. In the answered captures, those widths focus the caption, stream-flow and John Klein claims respectively. `CHECK-RESULTS.txt` records proof-level automated checks. `CREDITS.md` records the claim-to-passage coordinates, source material and font licenses. `TASTE.md` records the visual review and deviations from the Light Table design bible.
