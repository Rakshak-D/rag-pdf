# Light Table taste review

## If the logo were covered

- **Drop:** The empty, paper-shaped dashed outline sits beside a quiet conversation; the right reader shows no sample title, page count or inactive page controls. The “No PDF open” switch and “Put a PDF here” outline make the empty document state explicit.
- **Reading:** A full scanned PDF page occupies the brighter right side while a small contact sheet anchors the conversation to page order. The top-bar “Model ready” label names answer-model status rather than local file storage.
- **Answered:** Numbered claim-specific source chips and distinct fluorescent boxes connect the answer to the Link caption, stream-flow paragraph and John Klein paragraph on the real rendered page. The credited Link figure stays adjacent to the answer, so the screen reads as a page-and-evidence workspace.
- **Model missing:** The NASA page, selected stream-flow passage and credited figure remain inspectable while the amber “Ollama unavailable” status explains that local answering is unavailable.

## Design values

- Room: `#16140F`; conversation panel: `#211F19`; raised figure panel: `#29271F`; page sheet: `#EEE8D9`; evidence: `#D7FF3A`; unavailable status: `#F0B269`.
- Type: locally bundled IBM Plex Sans for interface/answers and IBM Plex Mono for page labels and compact metadata. No serif display face is used.
- Spacing: 68 px desktop top bar; conversation padding 30 px; reader padding 25 px; 16 px body base. At mobile widths, the top bar is 56 px and the conversation/reader stack vertically.
- The proof keeps the design-bible starting colors and bundled sans/mono direction. The highlight is a translucent chartreuse overlay so black PDF print remains readable; the static screenshot box is aligned to Poppler page text bounds.
- Quiet utility text was raised from #898373 to #A39D8E after contrast measurement showed the former fell below 4.5:1 on the conversation panel; the updated ratio is 6.10:1.
- Evidence links use a 44×44 px hit box while retaining a compact mono label. The reader controls have 44×44 px boxes to keep the shell touch-friendly.
- This remains a static composition: the question, answer, figure association and source highlight are hard-coded proof states. It does not demonstrate live upload, retrieval, generation, streaming, or production coordinate conversion.

## R1 corrections reviewed

- The prior shared `data-evidence="link"` mapping was the root of the source mismatch: all three sentences selected the same first highlight. Each answer sentence now has its own source ID, exact page-2 passage, numbered chip and Poppler-verified boxes. Hover or focus selects only that target; the leader endpoint is measured against its own first marked line.
- At 390×844, the selected page crop now preserves the PDF's 612:792 aspect ratio instead of being capped to the short reader viewport. The crop positions the selected passage within the visible reader area. The connector runs from the citation's trailing edge to a narrow outer gutter, then into the target's right edge; it does not cross answer text or the credited figure. The mobile answered capture focuses the John Klein claim and shows its four-line highlighted support with surrounding page text.
- In `drop`, the reader identity and page controls are hidden and the page counter is empty. The empty paper outline remains the reader focus.
- The status dot now says “Model ready” in the connected composition and “Ollama unavailable” in the unavailable composition. These are static design states, not runtime health checks.
- Palette, font families, component spacing and the overall Light Table composition were retained. The mobile crop correction removes a height cap that distorted the page; no new color or decorative treatment was added.

## R2 mobile evidence visibility

- **Before:** R1 positioned the first marked line. In the 390×844 browser measurement, the John Klein passage ended at y=843.94 in an effective visible reader clip ending at y=844, leaving only 0.06 px below its final highlighted line. The remainder of its highlighted passage was too close to the viewport edge for comfortable inspection.
- **After:** The crop now uses the union of every verified mark for the selected claim and positions that complete range inside the intersection of the viewport, reader pane, page area and clipping ancestors. At 390×844, all marks are visible with 22 px below the final mark; space above the first mark is 56.92 px for Link caption, 32.30 px for stream-flow and 13.75 px for John Klein. The source chip stays on screen, and the connector remains in the outer gutter.
- The original page is still rendered at its 612:792 aspect ratio. The page itself supplies the surrounding context; no replacement passage panel was added. The small mobile crop is the only visual adjustment. Desktop layout and typography remain unchanged.
- Manual review of `answered-link-caption-390.png`, `answered-stream-flow-390.png` and `answered-390.png` confirmed the complete respective highlights are visible, legible at capture scale, and surrounded by original PDF content. The credited figure remains visible in each capture. Automated coordinates verify clipping and clearance; visual readability is assessed manually.
- `capture.mjs` now waits until all selected marks are within the effective clip and the minimum clearance is met before capturing the main John Klein mobile state or the two additional focused mobile captures.

## Banned-look checklist

| Banned treatment | Result | Evidence |
|---|---|---|
| Blue primary button | Absent; there is no primary-action button in this static proof. | All four state screenshots |
| Large white rounded card on pale-blue page | Absent; the room is warm near-black and the PDF is a warm paper sheet. | All four state screenshots |
| Gradient background | Absent in the proof CSS. | `assets/reading-desk.css` scan |
| Centered serif hero | Absent; layout is split and IBM Plex Sans is used. | All four state screenshots |
| Numbered stepper | Absent. | All four state screenshots |
| Unresolved visual concern | At 390×844 the conversation remains above the reader; selecting a claim frames its full passage in the visible reader clip. The static proof does not provide production page scrolling, zoom controls or verified PDF.js coordinate conversion. | `shots/answered-*-390.png` |
