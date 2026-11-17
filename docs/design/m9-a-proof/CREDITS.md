# Source and asset provenance

## NASA factsheet

- Source PDF: `mars-science-laboratory.pdf`, the four-page Mars Science Laboratory/Curiosity factsheet.
- Repository source: `data/pdf/mars-science-laboratory.pdf`, read from protected `main` commit `7e9522618296af6a7f25652cc8161009d9a92685`; Git blob SHA-1 `62296a2ce54907e76bb05859524e0184ee351539`.
- NASA source URL: <https://science.nasa.gov/wp-content/uploads/2023/09/mars-science-laboratory.pdf>.
- Page renders: `assets/pages/page-01.png` through `page-04.png`, rendered from the source PDF at 130 dpi with Poppler `pdftoppm`; contact images `assets/pages/contact-1.png` through `contact-4.png` are rendered at 45 dpi. R5 first-run uses the genuine page-one render; the optional PDF sheet and contact strip use the page renders and contact images. Pages 1–3 support the cited proof; page 4 is retained as a source-derived proof asset.
- Render method: Poppler `pdftoppm`; page renders at 130 dpi and contact images at 45 dpi. The source PDF was not edited.
- Page-image accessible labels identify the page number and NASA Mars Science Laboratory/Curiosity factsheet, matching the displayed image. The R5 first-run preview label is “NASA factsheet, page 1, showing Gale Crater.”

## Suggested-question support

The first-run suggestions were checked against the source PDF with `pdftotext -layout`:

| Suggested question | Actual source support |
|---|---|
| What does the rock Link tell us about water on Mars? | Page 2 caption: “A rock outcrop called Link shows signs of being formed by the deposition of water.” Related passages identify stream-flow evidence in rounded pebbles and hardened sand and sustained liquid-water evidence in the John Klein sample. |
| How much power did the rover have at launch? | Page 3: “At launch, the generator provided about 110 watts of electrical power.” This is the rover’s radioisotope generator output, not the launch vehicle’s power. |
| Which crater did it land in, and what did it drill first? | Page 1 identifies Gale Crater; page 2 identifies the first drilled sample as coming from the “John Klein” rock target and records evidence of sustained liquid water. |

Each suggestion has a deterministic pre-authored answer fixture in the proof. These source checks establish factual support only; they are not retrieval or generation results.

## First-run page mark

The R5 first-run page preview uses the genuine `assets/pages/page-01.png` raster and places one lime mark over the source phrase “Gale Crater.” Poppler `pdftotext -bbox -f 1 -l 1` returned the words `Gale` at `(109.070, 201.220)–(128.710, 213.040)` and `Crater,` at `(131.490, 201.220)–(160.031, 213.040)` PDF points. The overlay spans their combined box `(109.070, 201.220)–(160.031, 213.040)` on the 612×792-point page, normalized to the unchanged 1105×1430 page render. The phrase is present in the first paragraph describing the rover's landing site. This verifies alignment on the Poppler raster only, not production PDF.js coordinates.

## Link figure and caption

- Figure: genuine Link outcrop image from `data/images_pymupdf/mars-science-laboratory/page_2_img_0.png`, copied into `assets/figures/link-outcrop.png`.
- Original extracted-figure Git blob: `dddaf8883e7b1d677569efd8c0afcea0fe9c1452`.
- Identity: NASA/JPL-Caltech/MSSS image PIA16188, “Link to a Watery Past.” NASA Photojournal identifies the image and credit: <https://science.nasa.gov/photojournal/link-to-a-watery-past/>.
- Visible caption: “A rock outcrop called Link shows signs of being formed by the deposition of water.”
- Adjacent visible credit: `NASA/JPL-Caltech/MSSS`.
- Visible alt description: “Curiosity’s Mastcam view of the Link rock outcrop on Mars, with rounded pebbles and a one-centimeter scale marker.”
- NASA media guidance consulted: <https://www.nasa.gov/nasa-brand-center/images-and-media/>. The factsheet and NASA Photojournal identify NASA/JPL-Caltech/MSSS as the source; no third-party copyright notice was observed on the consulted source page. This proof uses the factual page and credited image, does not use a NASA insignia as product branding, and preserves attribution. This is an asset-provenance check, not legal advice or a separate licence grant.

## Citation quotation and page targets

The Link answer citations use distinct, previously verified passages on PDF page 2. The remaining scripted fixtures add the source targets below. Citation popovers show a short source quotation and a page thumbnail; they do not contain method explanations or result panels.

1. **Link caption — figure.** Quote: “A rock outcrop called Link shows signs of being formed by the deposition of water.” Source is the italic caption beneath the Link photo. Verified boxes in PDF points: `(46.080, 217.376)–(282.285, 227.600)` and `(46.080, 229.376)–(74.692, 239.600)`.
2. **Stream flow — words.** Quote: “The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.” Verified boxes: `(44.500, 584.660)–(274.202, 596.480)`, `(44.500, 600.160)–(286.421, 611.980)`, `(44.500, 615.660)–(133.780, 627.480)`. The last box ends at “site.” before “Analysis,” on the printed line.
3. **John Klein — meaning.** Short popover quote: “geological and mineralogical evidence for sustained liquid water.” Full source passage: `first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,`. Verified boxes: `(314.640, 139.400)–(559.882, 151.220)`, `(314.640, 154.900)–(547.671, 166.720)`, `(314.640, 170.400)–(560.840, 182.220)`, `(314.640, 185.900)–(423.730, 197.720)`. The final box ends at “water,” before “other key elemental ingredients.”

4. **Gale Crater — exact words.** Page 1 says the rover was set down “at Gale Crater, using precision landing technology.” The first-run preview mark covers the exact two-word page phrase using the combined word bounds above. The answer fixture's citation target `gale-site` includes the three landing-site lines `(45.360, 201.220)–(227.082, 213.040)`, `(45.360, 216.720)–(229.670, 228.540)`, and `(45.360, 232.220)–(241.691, 244.040)`.
5. **Landing sequence — meaning.** Page 1 states that a parachute slowed descent, retrorockets were then used, and the upper stage acted as a sky crane to lower the rover. The displayed short quotation is “During the three minutes before touchdown, the spacecraft slowed its descent with a parachute.” The proof target `landing-sequence` spans the checked source lines with boxes `(262.800, 551.054)–(468.200, 562.874)`, `(262.800, 566.554)–(457.280, 578.374)`, `(262.800, 582.054)–(457.873, 593.874)`, `(262.800, 597.554)–(452.282, 609.374)`, `(262.800, 613.054)–(467.680, 624.874)`, `(262.800, 628.554)–(467.320, 640.374)`, and `(262.800, 644.054)–(307.800, 655.874)`.
6. **Launch generator power — exact words.** Page 3 states: “At launch, the generator provided about 110 watts of electrical power to operate the rover’s instruments, robotic arm, wheels, computers and radio.” The answer fixture states only the supported 110-watt electrical output. The target `power-launch` uses checked boxes `(156.411, 585.680)–(280.721, 597.500)` and `(44.500, 601.180)–(221.970, 613.000)`.

An additional uncited representative match uses the page-2 passage `Curiosity touched down right in an area where water once coursed vigorously over the surface.` Boxes: `(44.500, 538.160)–(289.750, 549.980)`, `(44.500, 553.660)–(269.189, 565.480)`, `(44.500, 569.160)–(279.422, 580.980)`.

### Coordinate method and limits

Poppler `pdftotext -bbox-layout` extracted word and line boxes from original PDF pages 1–3. Source boxes are expressed in the PDF page’s 612×792-point coordinate space with a top-left origin and normalized to the 1105×1430-pixel, 130 dpi Poppler page image. The overlays therefore match the static Poppler renders. They do not verify production pdf.js coordinate conversion or demonstrate live retrieval.

The `words`, `meaning`, and `figure` labels are **simulated method labels** attached to genuine passages for this design proof. They were not returned or classified by the production retrieval pipeline. Turning on **Show matches** displays only these on-page overlays; the answer itself is unchanged.

### Mobile passage view and sample-selection identity

At 390px, the proof computes a source-specific page scale from the selected passage width, with a 200% cap. Across the 27 source/height/snap checks, native-PDF zoom measured 110–115%, the smallest highlight-line rectangle was 15.66 CSS pixels high, minimum edge clearance was 9.20px, and the largest center deviation was 4.53%. The page raster and Poppler-coordinate overlays scale together without changing the 612:792 ratio. Positioning centers the full passage within the page viewport after intersecting clipping ancestors and excluding the floating toolbar. Screenshots show the John Klein passage at 390×844 across all three snaps; temporary visual-review captures also covered each source at 92%. The source remains the original page image; no extracted-text replacement panel is used.

R4's fixed-scale measurements are retained in the earlier report for historical comparison and are superseded by R5's nine-combination gate. They are not claimed as current results.

The Playwright measurements describe highlight rectangles, not glyph cap height. Manual review of the real page renders confirmed that all three selected source passages were readable at the measured mobile scale; the Link caption's narrowest line rectangle is 15.66px high. This is a static visual assessment, not a production PDF.js or screen-reader text-layer test. The proof does not offer a separate extracted-text card or alter the PDF page ratio.

The optional attachment interaction identifies the bundled sample by filename, byte length (`1,512,372`) and SHA-256 (`2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6`). Selecting another PDF changes the displayed filename and disables sample-page viewing; the proof does not parse or index that file. The deterministic answers are pre-authored and do not call a model or retrieval service.
## Fonts

Font files, exact SHA-256 values, source revisions, and local SIL Open Font License 1.1 texts for the selected proof families are listed in `FONT-LICENSES.md`: Newsreader for answers, Instrument Sans for interface text, and IBM Plex Mono for compact metadata. These assets are served locally from this proof directory.
