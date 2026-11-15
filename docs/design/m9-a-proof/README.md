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

In the answered state, each numbered source chip selects a separate verified passage on page 2: the Link caption, the stream-flow paragraph or the John Klein drilling paragraph. On narrow screens, the reader positions the complete selected passage and its page marks inside the visible reader clip, with 22px below the final mark in the checked 390×844 layout. The page aspect ratio is preserved and the leader routes through the outer gutter. The `drop` state has no sample title, page count or page controls. “Model ready” and “Ollama unavailable” are static composition states, not live service checks.

## Evidence

`shots/` contains the 12 full-viewport state captures at 1440×900, 1024×768 and 390×844, plus focused 390×844 captures for the Link caption and stream-flow passages. The main `answered-390.png` capture focuses John Klein. `CHECK-RESULTS.txt` records proof-level automated checks and measured mobile clip clearances. `CREDITS.md` records the claim-to-passage coordinates, source material and font licenses. `TASTE.md` records the before/after mobile review and deviations from the Light Table design bible.
