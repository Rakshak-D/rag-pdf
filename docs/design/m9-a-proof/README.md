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

## Evidence

`shots/` contains full-viewport browser captures at 1440×900, 1024×768 and 390×844. `CHECK-RESULTS.txt` records proof-level automated checks. `CREDITS.md` records the source material and font licenses. `TASTE.md` records the visual review and deviations from the Light Table design bible.