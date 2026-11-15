# M9-A-R3 visual review

These are human screenshot judgments, not automated accessibility or retrieval findings. The screenshots were inspected at 1440×900, 1024×768, and 390×844. `CHECK-RESULTS.txt` contains the separate browser measurements.

## Taste review by state

### First run

**If the logo were covered:** The sample name and three source-supported questions sit directly above the composer, so the first interaction concerns an actual PDF. There is no empty viewer column or upload step competing with the conversation.

**Against a generic AI chat app:** The sample-specific prompts and document name establish the reading task before a conversation begins; adding a PDF and opening its page remain separate optional actions.

**Calm and distinct:** The quiet spacing and restrained status treatment have a calm pace. The dark room is ours; selecting a citation reveals the physical paper sheet and a lime highlighter mark, while the default screen keeps both document viewing and adding a PDF optional.

### Answered, PDF closed

**If the logo were covered:** The answer is set as continuous reading text, and the actual Link photograph with its NASA credit sits directly under the paragraph that cites it. Small numbered chips identify passages without a second evidence panel.

**Against a generic AI chat app:** The credited NASA figure is attached to the relevant claim, and each numbered citation refers to source evidence rather than an unrelated message decoration.

**Calm and distinct:** The sentence spacing and low-noise controls feel measured. The near-black room, optional physical paper sheet, and fluorescent lime source marks make the reading language specific to this product.

### Answered, PDF open

**If the logo were covered:** The same answer remains visible beside a page image with the cited passage highlighted. The page number and contact strip give document context without turning retrieval into a separate dashboard.

**Against a generic AI chat app:** The source sheet is the original factsheet page with its location and passage mark; the answer remains visibly tied to that page.

**Calm and distinct:** The sheet arrives quietly and the answer retains its reading rhythm. The dark room, warm physical paper, and lime passage highlight remain the signature; no connector line is needed to explain the citation.

### Answered, matches shown

**If the logo were covered:** Multiple source marks sit on the original page beside compact method labels. The selected citation remains strongest, while the other marks stay faint and do not alter the answer.

**Against a generic AI chat app:** Simulated method labels appear on the document page beside source marks, making evidence inspection part of reading rather than a separate score panel.

**Calm and distinct:** The toolbar is restrained and the page holds the detail. Our dark room, physical paper, and lime highlight make the evidence visible without adding result cards.

### Model unavailable

**If the logo were covered:** The amber status names the local service problem, and the page-reading action remains available. No generated answer is shown in this state.

**Against a generic AI chat app:** The local-model problem does not block reading the sample, and the open PDF is shown without pretending a model answered.

**Calm and distinct:** The single quiet error line and open conversation space keep the state calm. The dark room remains; opening the sample shows the physical paper sheet and the lime passage mark, separate from model readiness.

### Reading progress

**If the logo were covered:** A real page image and its numbered contact strip make the document the subject. The closed viewer state is not represented by a blank reserved pane.

**Against a generic AI chat app:** The page number and contact strip keep document location visible as the primary task, without inventing a conversation or answer.

**Calm and distinct:** The scan line is a restrained progress cue rather than a dashboard animation. The dark room, physical paper sheet, and lime active-page accent distinguish the visual language.

## Two aspects of Claude-like calm; what remains ours

The generous whitespace and low-emphasis utility controls share a quiet reading pace. The page itself is still the product’s proof: a warm physical-looking sheet against a dark room, with lime used only to mark evidence. The proof does not reuse another product’s logo, component, or layout asset.

## Direct review questions

- **Is it still boxy?** No large answer or conversation container is visible. The only substantial rounded surface is the fixed composer; small suggestion pills, the user-message pill, citations, and the temporary citation popover have distinct interaction roles. The PDF is an unrounded page inside a sheet.
- **Does copy sound like a slogan?** No. The first screen names the sample, offers three short questions, and labels the composer. The unavailable state states the condition and the reading action.
- **Is the PDF optional?** Yes. The page is closed by default in a fresh context. A citation or an explicit page preference opens it; Escape and Close PDF dismiss it.
- **Is adding a PDF optional?** Yes. The composer remains enabled for the checked-in sample; the plus button only selects a file. It does not upload or ingest it.
- **Does mobile look composed rather than compressed?** Mostly. The open sheet fills the width and preserves the page ratio; the active citation is framed above the composer, source targets are repositioned inside the page clip, and there is no horizontal scroll. In this narrow layout, opening a source scrolls earlier chat context upward; the user can scroll back to it.
- **Which mark is strongest at 16px?** No winner is selected. Candidate A reads as an open C, B as a waveform, and C as a section sign; their rendered monochrome silhouettes are distinct, with B's signal-like reading the main identity question.

## Design-value changes from the previous Light Table brief

| Value | R3 decision | Reason |
|---|---|---|
| Layout | Replaced the default split view with a centered 720px conversation; the PDF sheet opens only on citation or remembered explicit preference. | Satisfies the conversation-first direction and keeps viewing optional. |
| Message treatment | Assistant text has no background or border; the user turn is a compact, soft pill. | Removes the reviewed card-heavy look and retains clear speaker distinction. |
| Answer type | Newsreader at 18px with 29.88px line-height on desktop; 17px with 27.88px line-height on mobile. | Gives long answers a readable serif surface while keeping narrow screens legible. |
| Interface type | Instrument Sans; IBM Plex Mono remains for page and source metadata. | Font comparison favored the clearer humanist sans; mono is limited to compact labels. |
| Typeface comparison | Source Serif 4 and Geist are bundled and compared locally, but not selected. | Required candidates are measured without silent system-font substitution. |
| Conversation width | 720px maximum in the closed state; desktop open state uses the available left area with transform movement. | Keeps reading lines comfortable and allows a 55vw source sheet without a permanent empty pane. |
| Mobile composition | Viewer is a bottom sheet; the selected conversation passage is moved above the fixed composer and the inline figure is reduced to 210px while the sheet is open. | Keeps the cited sentence, figure credit, and page selection inspectable around the narrower viewport. |
| Citation target | 44px interactive target with a 21px lime number mark. | Improves keyboard and touch target size while keeping the mark visually compact. |
| Paper edge | Full page keeps near-square corners; mobile sheet retains a subtle 12px top radius. | Makes the page read as paper and distinguishes the sheet boundary without a large card style. |
| Color | Retained room `#16140F`, panel `#211F19`, raised `#29271F`, paper `#EEE8D9`, lime `#D7FF3A`, amber `#F2B567`. | The existing palette already met contrast checks and the owner retained it. |
| Motion | Sheet uses 200ms transform/opacity; passage mark pulses once; current page scan uses transform only. Reduced motion disables all three. | Keeps motion attached to source interaction and reading position. |
| Visible copy | Removed slogans, upload instructions, and separate retrieval explanations; the first screen is sample + three questions + composer. | Makes questioning immediate and removes the impression that upload or viewer setup is mandatory. |
| Evidence view | Source details appear as an inline citation chip, short quote popover, and page overlay; no connector lines or separate evidence cards. | Keeps source inspection close to the answer and follows the owner’s interpretation. |
| Attachment behavior | Local file selection is optional; only the byte-verified bundled NASA sample reveals **Open sample pages**. | Demonstrates a proof-only reading transition without claiming upload, parsing, or indexing for arbitrary PDFs. |

## Remaining visual concern

The default desktop first-run state intentionally has substantial open space between the question suggestions and the composer. It feels calm and gives later turns room, but can read as vacant before the user asks. Showing a page by default would make PDF viewing compulsory, so the proof keeps the citation-open interaction as the reveal.

## M9-A-R3-F1 review corrections

### 1024px answer visibility

The prior 1024×768 capture showed the last John Klein sentence beneath the fixed composer. The corrected layout reserves a separate viewport region above the composer and scrolls the conversation inside that region. Character-range checks measured zero visible answer glyphs crossing the composer at 390, 1024, and 1440px. In the scrolled 1024 capture, the user question, answer paragraphs, figure, and credit remain visible, with the final sentence ending above the composer.

### Mobile cited passage

The mobile PDF sheet keeps the original page image and its source marks in one proportionally scaled frame. At 390×844, opening any citation uses 2.5× magnification and two-axis scrolling; all source marks fit inside the visible clipping rectangle. The Link, stream-flow, and John Klein screenshots show real page text around each highlight. The HTML proof still cannot establish how the production pdf.js viewer will position the same evidence.

### First-run and attachment behavior

Only the first suggestion has a deterministic scripted answer. Its question is repeated above the answer, and the real NASA figure, adjacent credit, and three citations appear without an API request. The other supported questions stay prompts; the proof says when an answer has not been scripted. Selecting the exact bundled NASA sample exposes **Open sample pages**; opening it reveals the static page-three image. Other PDFs, including a same-named file with different bytes, remain filename-only. No percentage, job, indexing, parsing, or generated answer is implied.

### Unavailable model

The amber status now reads **Ollama unavailable — start Ollama.** The independent **Open PDF** action still opens the static sample page without an answer or retrieval mark.

### Mark candidates at 16px

- **A · Open passage:** an incomplete loop with a short highlighted stroke. At 16px it can read as an open C; it does not read as a page frame.
- **B · Reading current:** a continuous uneven wave. It remains distinct, though the waveform can resemble a generic signal mark at small size.
- **C · Section knot:** a custom section-sign contour with a lime crossing. It is a familiar typographic citation symbol rather than an invented pictogram.

The pixel captures of the 16px monochrome samples are distinct. None is a file outline, code bracket, or folded-note icon. No winner is selected; the waveform’s resemblance to a generic signal is the main identity question for review.
