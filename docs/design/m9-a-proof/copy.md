# R4 visible-copy inventory

The Playwright audit counts visible alphabetic words of interface chrome at 1440×900. It excludes answer text, the user question, suggested questions, source quotations, rasterized PDF text, figure caption and figure credit. Numeric page references and citation indices are identifiers, not copy words. Suggested-question words are reported separately. Hidden `.sr-only` labels are listed for accessibility but do not count as visible copy.

## Visible identity and first run

| Copy | Location or use |
|---|---|
| `RAG-PDF` | Product wordmark |
| `NASA Curiosity fact sheet` | Active sample title |
| `Sample, 4 pages` | Sample metadata |
| `Ask a question` | Composer placeholder and accessible label |
| Three exact suggested questions below | User prompts; excluded from chrome word cap and counted separately |

1. “What does the rock Link tell us about water on Mars?”
2. “How much power did the rover have at launch?”
3. “Which crater did it land in, and what did it drill first?”

The three prompts total 32 words under the checker’s word-boundary rule. First-run chrome is 10 words; the reported combined visible total is 42.

## Header and composer controls

- `New chat` — icon accessible name and tooltip.
- `Open menu` — header menu accessible name. The menu has the `Settings` item.
- `Show recent PDFs` — document-menu control accessible name; its panel reads `Recent PDFs` and lists the sample title.
- `Add a PDF` — plus button accessible name and tooltip.
- `Choose a PDF` — file input accessible name.
- `Send question` — send button accessible name when available.
- `Preparing answer` / `Model unavailable` — send control names in static status variants.
- `Question composer` — form accessible name.
- `Remove PDF from this conversation` — active-document removal name; the source file is not deleted.
- `Open or close NASA Curiosity fact sheet, 4 pages` — sample document control name. For other selected files the control states that page viewing is unavailable.

## Attention, local selection, and feedback

- Starting state: “Getting the model ready. The first answer can take a minute.” This exact required sentence contains the separately banned phrase “Model ready”; see the documented C33 conflict in `CHECK-RESULTS.txt`.
- Unavailable state: “Can’t reach the model. Start Ollama, then check again.” Actions: `Check again`, `How to start it`.
- Local help: “Open the local model app from the Start menu, then select Check again.” The help panel has a `Close` button. The check action does not contact a live service.
- Invalid selection: “Choose a PDF file.”
- Other local PDF selection: “This proof displays the file name but does not read the PDF.”
- Missing scripted answer: “This proof has no saved answer for that question.”
- Service-check action response: “This proof does not check the local service.”
- Active file type label: `PDF`.
- Exact bundled sample action: `Open sample pages`.

## Citations and PDF controls

- Inline citations display numeric indices `1`, `2`, `3`; accessible names identify the source number, page, and open action.
- Citation popover label: `Page 2`, followed by the short source quotation documented in `CREDITS.md`.
- PDF sheet accessible name: `NASA Curiosity fact sheet` (screen-reader title; not repeated visibly in the sheet header).
- `Close PDF`; `Previous page`; `Next page`; `Zoom out`; `Zoom in`; `Show matches` / `Hide matches`. Repeated zoom-out returns the page to fit width; there is no separate visible Fit page button.
- Page state: numeric page and total, for example `2 / 4`; the separate composer status is `Page 2/4`.
- `Cited` and `Uncited` identify the solid cited mark and dotted uncited mark.
- Method tags: `Exact words`, `Meaning`, `Figure`. These are simulated classifications, not retrieval results.
- `Change PDF viewer height` and `PDF viewer height` are accessible names for the sheet grip. Snap choices are 30%, 55%, and 92%.
- Contact-strip controls are labelled `Go to page 1` through `Go to page 4`.

## Settings and accessibility-only strings

- Settings panel: `Settings`; “Model checks run on this computer. This proof does not check a live service.”; `Close settings`.
- Visible page-help panel: “Open the local model app from the Start menu, then select Check again.”; `Close`.
- Skip-link label: `Skip to conversation`.
- Live announcements include “Page 2 opened with the cited passage highlighted.”, “NASA Curiosity fact sheet selected.”, and “PDF removed from this conversation. The original file remains available.”
- Other accessible names include `Conversation controls`, `Conversation`, `Model connection`, `PDF controls`, `PDF page`, `Pages in this PDF`, `Passage marks`, `Source methods`, `Suggested questions`, and `Answer`.
- Page images and source figure have descriptive alt text recorded in `CREDITS.md`.

## Other fixture copy

The answer gallery is headed `Answer shapes` and labels its five examples `Fixture A` through `Fixture E`. Their questions and answer passages are fixture content rather than interface chrome. The no-answer example reads: “I couldn’t find this in the PDF. The factsheet describes the descent sequence, but it gives no single exact speed.” It is a proof fixture, not a retrieval result.

## Measured visible interface words

Latest Playwright measurements at 1440×900; see the complete per-state records in `CHECK-RESULTS.txt`.

| State | Chrome words | Suggested-question words | Combined visible total |
|---|---:|---:|---:|
| first-run | 10 | 32 | 42 |
| answered-closed | 10 | 0 | 10 |
| answered-open | 13 | 0 | 13 |
| answered-matches | 22 | 0 | 22 |
| model-unreachable | 25 | 0 | 25 |
| starting | 21 | 0 | 21 |
| reading-progress | 13 | 0 | 13 |
| answer-shapes | 4 | 0 | 4 |

The starting state passes the 25-word limit but fails the banned-phrase scan because of the explicit R4 wording conflict. That failure is retained and disclosed rather than silently rewriting the required sentence.
