# R5 visible-copy inventory

The Playwright audit counts visible interface chrome at 1440×900. It excludes user questions, assistant answers, suggestions, citation quotations, PDF text, figure captions and figure credits from the 25-word chrome limit. Suggested questions are counted separately. Page numbers and citation indices are source references, not copy words. Hidden screen-reader labels are listed for completeness.

## First run

| Visible or accessible string | Location |
|---|---|
| `RAG-PDF` | Provisional header wordmark |
| `Mars Science Laboratory/Curiosity factsheet` | Active sample identity |
| `4 pages` | Sample page count |
| `Ask a question` | Composer placeholder and label |
| `Add a PDF` | Composer attachment control |
| `Send question` | Composer submit control |
| `Open or close Mars Science Laboratory/Curiosity factsheet, 4 pages` | Sample page-view control accessible name |

The three visible suggestions are excluded from chrome words and counted separately:

1. “What does the rock Link tell us about water on Mars?”
2. “How much power did the rover have at launch?”
3. “Which crater did it land in, and what did it drill first?”

The complete suggested-question answers are pre-authored fixtures; see `CREDITS.md`. They do not use a model or retrieval service.

## Header and document controls

- `New chat` — header action name and title.
- `Open menu`; menu item `Settings`.
- `Show recent PDFs`; panel title `Recent PDFs`; sample item `Mars Science Laboratory/Curiosity factsheet`.
- `Remove PDF from this conversation` — removes the selection from this proof conversation only.
- `Choose a PDF` — file input accessible name.
- `PDF` — selected-file type marker.
- `Selected PDF` — selected-file metadata.
- `Choose a PDF file.` — invalid-file feedback.
- `This proof displays the file name but does not read the PDF.` — arbitrary-file feedback.
- `PDF removed from this conversation. The original file remains available.` — removal announcement.
- `Mars Science Laboratory/Curiosity factsheet selected.` — sample-selection announcement.

## Unavailable model

- Exact attention line: `Can't reach the model. Start Ollama, then check again.`
- `Check again` — outlined local prototype action; reports `This proof does not check the local service.` and never claims availability.
- `How to start it` — opens local instructions: `Start Ollama from the Start menu, then select Check again.`
- `Close` — closes the local help panel and returns focus.
- `Model unavailable` — send control's accessible name in this state.

The unreachable state is a static composition. It does not query Ollama. The PDF can still be opened independently.

## Citations and PDF viewer

- Citation chips: numeric source indices; accessible names identify the source number and page.
- Citation popover: `Page 1`, `Page 2`, or `Page 3`, a page thumbnail, and the short source quotation recorded in `CREDITS.md`.
- `Close PDF`; `Previous page`; `Next page`; `Zoom out`; `Zoom in`; `Show matches` / `Hide matches`.
- Viewer grip: `PDF viewer height` / `Change PDF viewer height, 30/55/92 percent`.
- `Go to page 1` through `Go to page 4` — thumbnail controls.
- `Page N / 4` — current viewer page; `Page N/4` — composer progress; `Reading page N of 4` — progress accessible name.
- `Cited` and `Uncited` — legend for solid cited marks and dotted uncited marks.
- `Exact words`, `Meaning`, and `Figure` — simulated method labels in the PDF gutter or mobile popover.
- `Open or close Mars Science Laboratory/Curiosity factsheet, 4 pages` — the sample page-view control; arbitrary PDFs are not parsed by the proof.

## Other visible or accessible interface copy

- `Question composer`; `Conversation controls`; `Conversation`; `PDF controls`; `PDF page`; `Pages in this PDF`; `Passage marks`; `Source methods`; `Suggested questions`; `Answer`; `Skip to conversation`.
- Settings panel: `Settings`; `Model checks run on this computer. This proof does not check a live service.`; `Close settings`.
- Missing fixture feedback: `This proof has no saved answer for that question.` This can occur only for an unsupported manually typed question; all three visible suggestions are wired to complete source-backed fixtures.
- Figure caption and credit are source-provided content, not interface chrome; they appear beside the real NASA/JPL-Caltech/MSSS figure.
- The sample's full name is used as a document identity; no standalone `Curiosity` label or model-ready status appears.

## Measured visible words

At 1440×900, the Playwright audit counts the composer placeholder as chrome and reports the three suggested questions separately. The largest state is the model-unavailable composition at the 25-word limit.

| State | Chrome-only words | Including visible suggestions | Limit |
|---|---:|---:|---:|
| First run | 10 | 42 | 25 chrome words |
| Answered, PDF closed | 10 | 10 | 25 chrome words |
| Answered, PDF open | 13 | 13 | 25 chrome words |
| Answered, matches shown | 22 | 22 | 25 chrome words |
| Model unavailable | 25 | 25 | 25 chrome words |
| Reading progress | 13 | 13 | 25 chrome words |
| Answer shapes | 4 | 4 | 25 chrome words |

Combined totals add the 32 words in the three visible first-run questions; they are questions rather than interface chrome. The checker also verifies the banned-phrase list, sample identity, failure copy, placeholder and all visible-state restrictions. Exact measurements and the visible text sample are in `CHECK-RESULTS.txt`.
