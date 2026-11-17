# R5 visual review

## Required taste check

**Does first-run now look like a reading tool for documents, or still like a generic chat app?** It reads as a document tool because a genuine NASA page with a source-verified lime highlight is the largest object, while the sample identity and three document-specific questions sit beside the composer.

## Screen-by-screen reading

- **First run:** The bright paper page, exact highlighted words, and sample-specific questions establish the document as the subject. The composer remains available and the preview does not require opening the PDF viewer.
- **Answered, PDF closed:** Newsreader answer text remains unboxed; inline source numbers attach each factual claim to its citation.
- **Answered, PDF open:** The original page render and its aligned highlight sit in the optional sheet; the user bubble has a measured gap from the sheet.
- **Matches shown:** Source marks and method labels remain on the original page; the toggle begins off and does not alter the answer.
- **Model unavailable:** One quiet line gives the correction; the outlined retry is visibly distinct, and the page can still be opened without a generated answer.
- **Reading progress:** Page thumbnails and the page reference show location without invented percentages or job states.
- **Answer shapes:** The gallery keeps a single reading column; Fixture C is now four direct, sourced sentences and Fixture E retains its earlier insufficient-evidence wording.

## Visual system and R5 changes

- **Room and paper:** Existing near-black `#16140F`, warm paper `#F4F1E8`, and evidence lime `#D7FF3A` remain. The first-run page preview uses the paper tone and the lime mark is positioned over the verified “Gale Crater” phrase.
- **Type:** Bundled Newsreader answers, Instrument Sans controls, and IBM Plex Mono page references remain unchanged.
- **Spacing:** The answered R4 conversation remains accepted. First run is the scoped exception: a two-column page-and-questions layout on desktop and a page, stacked questions, and bottom composer on mobile.
- **Citations:** Popovers now follow the cited paragraph in normal flow. This avoids covering the cited words, the user turn, and subsequent answer content; citation switching within one paragraph keeps a stable location.
- **Mobile evidence:** Zoom is derived from each source passage and the viewport width, and the highlight is centered in the viewer area after excluding the floating toolbar. The full nine-combination geometry report is in `CHECK-RESULTS.txt`; visual readability still requires the screenshots to be inspected manually.
- **Marks:** Candidate A remains the provisional marked-page symbol. Candidate B remains only the two-tone wordmark study. Candidate C and B monochrome comparisons were removed; no permanent mark is selected.

## Remaining concerns

This is a static proof. Its local answer fixtures, sample highlighting, and model-unavailable state do not establish production retrieval, generation, live health checks, file parsing, or PDF.js coordinate conversion. The exact NASA source text and Poppler coordinates are documented in `CREDITS.md`; future production highlighting must be independently verified against the real PDF.js page.
