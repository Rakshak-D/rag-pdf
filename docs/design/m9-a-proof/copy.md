# Interface-copy inventory

The word counts below are measured from the rendered 1440×900 states by `check.mjs`. They count visible interface text and controls. They exclude the static user-authored question and answer, NASA page imagery/body text, the figure caption and credit, and the source quotation in a citation popover; those source-derived strings are inventoried in `CREDITS.md`. The focus-only skip link and accessibility names are listed below but are not visible in an unfocused screen and are not included in the counts.

## Shared interface strings

| String | State or control |
|---|---|
| RAG-PDF | Header wordmark and browser title prefix |
| Model ready | Static connected-model composition; not a live health check |
| Ollama unavailable | Static unavailable-model composition and model note |
| Skip to conversation | Focus-only skip link |
| Add PDF | Plus button accessible name and tooltip |
| Ask a question | Composer label and placeholder |
| Send question | Send button accessible name |
| Question composer | Composer accessible name |
| Choose a PDF file. | Invalid local file-selection message |
| Question not sent. | Static composer response; no generation request is made |
| Remove selected PDF | Selected-file removal button accessible name |
| PDF | Selected-file type label, shown only after local file selection |
| Curiosity | NASA sample document name |

## First run

| String | Meaning |
|---|---|
| What does Link show? | Figure question |
| How many watts at launch? | Number question |
| Which crater and drill target? | Cross-page question, supported by the factsheet's page 1 and page 2 |

## Answered states

The visible user question and three answer paragraphs are source/demo content, not interface copy. Their exact wording appears in `proof-static-not-functional.html` and is verified against the passages in `CREDITS.md`.

| String | Meaning |
|---|---|
| 1, 2, 3 | Inline citation marks |
| Open source 1, page 2 | First citation accessible name; numbers change for citations 2 and 3 |
| Page 2 | Citation quotation popover page label |
| Close PDF | PDF sheet close control accessible name and tooltip |

## PDF sheet and reading progress

| String | State or control |
|---|---|
| Curiosity factsheet | PDF sheet title |
| 2 / 4, 3 / 4 | Current page and total page count |
| Show matches / Hide matches | Page-overlay toggle and its pressed state |
| Zoom out / Zoom in / Fit page | Page sizing controls |
| PDF page | Page viewport accessible name |
| Reading progress | Contact-sheet accessible name |
| 01, 02, 03, 04 | Contact-sheet page labels |
| figure, words, meaning | Simulated source-method labels shown only with matches enabled |

## Model unavailable

| String | Meaning |
|---|---|
| Open the PDF to read this sample. | Available reading action when the local answer model is unavailable; no answer is shown in this state |
| Open PDF | Explicit action that opens the sample page without showing a generated answer or retrieval highlight |

## Source-derived strings inventoried separately

The citation popovers contain genuine source quotations of at most 25 words. The Link figure caption, NASA/JPL-Caltech/MSSS credit, alt description, page-image descriptions, and passage text are documented in `CREDITS.md`. They are not interface slogans and are excluded from the state word counts.

## Measured visible interface words per 1440×900 state

These values are regenerated in `CHECK-RESULTS.txt`:

| State | Visible interface words |
|---|---:|
| first-run | 21 |
| answered-closed | 6 |
| answered-open | 17 |
| answered-matches | 21 |
| model-missing | 16 |
| reading-progress | 16 |

## Copy exclusions

The banned-expression scan is performed on visible rendered text, including status, controls and tooltips currently in the DOM. Document quotations/captions/credits and answer/user content are excluded from the 25-word UI-copy limit, but still remain documented source content. The alternative product names appear only in proof documentation, never in the proof UI.
