# M9-A-R3 visual review

These are human screenshot judgments, not automated accessibility or retrieval findings. The screenshots were inspected at 1440×900, 1024×768, and 390×844. `CHECK-RESULTS.txt` contains the separate browser measurements.

## Taste review by state

### First run

**If the logo were covered:** The sample name and three source-supported questions sit directly above the composer, so the first interaction concerns an actual PDF. There is no empty viewer column or upload step competing with the conversation.

**Calm and distinct:** The quiet spacing and restrained status treatment have a calm pace. The dark room is ours; selecting a citation reveals the physical paper sheet and a lime highlighter mark, while the default screen keeps both document viewing and adding a PDF optional.

### Answered, PDF closed

**If the logo were covered:** The answer is set as continuous reading text, and the actual Link photograph with its NASA credit sits directly under the paragraph that cites it. Small numbered chips identify passages without a second evidence panel.

**Calm and distinct:** The sentence spacing and low-noise controls feel measured. The near-black room, optional physical paper sheet, and fluorescent lime source marks make the reading language specific to this product.

### Answered, PDF open

**If the logo were covered:** The same answer remains visible beside a page image with the cited passage highlighted. The page number and contact strip give document context without turning retrieval into a separate dashboard.

**Calm and distinct:** The sheet arrives quietly and the answer retains its reading rhythm. The dark room, warm physical paper, and lime passage highlight remain the signature; no connector line is needed to explain the citation.

### Answered, matches shown

**If the logo were covered:** Multiple source marks sit on the original page beside compact method labels. The selected citation remains strongest, while the other marks stay faint and do not alter the answer.

**Calm and distinct:** The toolbar is restrained and the page holds the detail. Our dark room, physical paper, and lime highlight make the evidence visible without adding result cards.

### Model unavailable

**If the logo were covered:** The amber status names the local service problem, and the page-reading action remains available. No generated answer is shown in this state.

**Calm and distinct:** The single quiet error line and open conversation space keep the state calm. The dark room remains; opening the sample shows the physical paper sheet and the lime passage mark, separate from model readiness.

### Reading progress

**If the logo were covered:** A real page image and its numbered contact strip make the document the subject. The closed viewer state is not represented by a blank reserved pane.

**Calm and distinct:** The scan line is a restrained progress cue rather than a dashboard animation. The dark room, physical paper sheet, and lime active-page accent distinguish the visual language.

## Two aspects of Claude-like calm; what remains ours

The generous whitespace and low-emphasis utility controls share a quiet reading pace. The page itself is still the product’s proof: a warm physical-looking sheet against a dark room, with lime used only to mark evidence. The proof does not reuse another product’s logo, component, or layout asset.

## Direct review questions

- **Is it still boxy?** No large answer or conversation container is visible. The only substantial rounded surface is the fixed composer; small suggestion pills, the user-message pill, citations, and the temporary citation popover have distinct interaction roles. The PDF is an unrounded page inside a sheet.
- **Does copy sound like a slogan?** No. The first screen names the sample, offers three short questions, and labels the composer. The unavailable state states the condition and the reading action.
- **Is the PDF optional?** Yes. The page is closed by default in a fresh context. A citation or an explicit page preference opens it; Escape and Close PDF dismiss it.
- **Is adding a PDF optional?** Yes. The composer remains enabled for the checked-in sample; the plus button only selects a file. It does not upload or ingest it.
- **Does mobile look composed rather than compressed?** Mostly. The open sheet fills the width and preserves the page ratio; the active citation is framed above the composer, source targets are repositioned inside the page clip, and there is no horizontal scroll. In this narrow layout, opening a source scrolls earlier chat context upward; the user can scroll back to it.
- **Which original mark is strongest at 16px?** Candidate A, “Page window,” because its page outline and one emphasized reading line remain distinct at the smallest comparison size. This is a review preference, not a chosen permanent product mark.

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
| Attachment behavior | Local file selection is shown as an optional filename chip only. | Demonstrates the optional route without claiming upload or indexing. |

## Remaining visual concern

The default desktop first-run state intentionally has substantial open space between the question suggestions and the composer. It feels calm and gives later turns room, but can read as vacant before the user asks. Showing a page by default would make PDF viewing compulsory, so the proof keeps the citation-open interaction as the reveal.
