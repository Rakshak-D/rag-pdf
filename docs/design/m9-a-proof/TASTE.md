# Light Table taste review

## If the logo were covered

- **Drop:** The empty, paper-shaped dashed outline sits beside a quiet conversation and document controls, making the blank page itself the invitation to read.
- **Reading:** A full scanned PDF page occupies the brighter right side while a small contact sheet anchors the conversation to page order.
- **Answered:** The answer's page chips, matching fluorescent marks on the real rendered page, and the linked figure credit form a visible trail from claim to source.
- **Model missing:** The NASA page, marked passage and credited figure remain inspectable while a small amber status explains that only local answering is unavailable.

## Design values

- Room: `#16140F`; conversation panel: `#211F19`; raised figure panel: `#29271F`; page sheet: `#EEE8D9`; evidence: `#D7FF3A`; unavailable status: `#F0B269`.
- Type: locally bundled IBM Plex Sans for interface/answers and IBM Plex Mono for page labels and compact metadata. No serif display face is used.
- Spacing: 68 px desktop top bar; conversation padding 30 px; reader padding 25 px; 16 px body base. At mobile widths, the top bar is 56 px and the conversation/reader stack vertically.
- The proof keeps the design-bible starting colors and bundled sans/mono direction. The highlight is a translucent chartreuse overlay so black PDF print remains readable; the static screenshot box is aligned to Poppler page text bounds.
- Quiet utility text was raised from #898373 to #A39D8E after contrast measurement showed the former fell below 4.5:1 on the conversation panel; the updated ratio is 6.10:1.
- Evidence links use a 44×44 px hit box while retaining a compact mono label. The reader controls have 44×44 px boxes to keep the shell touch-friendly.
- This remains a static composition: the question, answer, figure association and source highlight are hard-coded proof states. It does not demonstrate live upload, retrieval, generation, streaming, or production coordinate conversion.

## Banned-look checklist

| Banned treatment | Result | Evidence |
|---|---|---|
| Blue primary button | Absent; there is no primary-action button in this static proof. | All four state screenshots |
| Large white rounded card on pale-blue page | Absent; the room is warm near-black and the PDF is a warm paper sheet. | All four state screenshots |
| Gradient background | Absent in the proof CSS. | `assets/reading-desk.css` scan |
| Centered serif hero | Absent; layout is split and IBM Plex Sans is used. | All four state screenshots |
| Numbered stepper | Absent. | All four state screenshots |
| Unresolved visual concern | At 390×844 the conversation remains above the reader; the cited page is zoomed to the highlighted passage in the visible reader window. The full page remains a vertical scroll/viewer responsibility for a later functional implementation. | `shots/*-390.png` |