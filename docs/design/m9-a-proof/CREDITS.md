# Source and asset provenance

## NASA factsheet

- Source PDF: `mars-science-laboratory.pdf`, the four-page Mars Science Laboratory/Curiosity factsheet.
- Repository source: `data/pdf/mars-science-laboratory.pdf`, read from protected `main` commit `7e9522618296af6a7f25652cc8161009d9a92685`; Git blob SHA-1 `62296a2ce54907e76bb05859524e0184ee351539`.
- NASA source URL: <https://science.nasa.gov/wp-content/uploads/2023/09/mars-science-laboratory.pdf>.
- Page renders: `assets/pages/page-02.png`, `assets/pages/page-03.png`; page contact images `assets/pages/contact-1.png` through `contact-4.png`.
- Render method: Poppler `pdftoppm`; page renders at 130 dpi and contact images at 45 dpi. The source PDF was not edited.
- Page-image accessible labels: “Page 2 of the NASA Mars Science Laboratory/Curiosity factsheet” and “Page 3 of the NASA Mars Science Laboratory/Curiosity factsheet,” matching the image shown.

## Suggested-question support

The first-run suggestions were checked against the source PDF with `pdftotext -layout`:

| Suggested question | Actual source support |
|---|---|
| What does Link show? | Page 2 caption: “A rock outcrop called Link shows signs of being formed by the deposition of water.” |
| How many watts at launch? | Page 3: “At launch, the generator provided about 110 watts of electrical power.” The prompt only proposes a question; no answer is prefilled. |
| Which crater and drill target? | Page 1 identifies the rover’s landing site as Gale Crater. Page 2 identifies the first drilled sample as coming from the rock target called “John Klein.” The question spans two pages. |

These checks establish that the static suggestions are answerable from the factsheet. They are not retrieval or generation results.

## Link figure and caption

- Figure: genuine Link outcrop image from `data/images_pymupdf/mars-science-laboratory/page_2_img_0.png`, copied into `assets/figures/link-outcrop.png`.
- Original extracted-figure Git blob: `dddaf8883e7b1d677569efd8c0afcea0fe9c1452`.
- Identity: NASA/JPL-Caltech/MSSS image PIA16188, “Link to a Watery Past.” NASA Photojournal identifies the image and credit: <https://science.nasa.gov/photojournal/link-to-a-watery-past/>.
- Visible caption: “A rock outcrop called Link shows signs of being formed by the deposition of water.”
- Adjacent visible credit: `NASA/JPL-Caltech/MSSS`.
- Visible alt description: “Curiosity’s Mastcam view of the Link rock outcrop on Mars, with rounded pebbles and a one-centimeter scale marker.”
- NASA media guidance consulted: <https://www.nasa.gov/nasa-brand-center/images-and-media/>. The factsheet and NASA Photojournal identify NASA/JPL-Caltech/MSSS as the source; no third-party copyright notice was observed on the consulted source page. This proof uses the factual page and credited image, does not use a NASA insignia as product branding, and preserves attribution. This is an asset-provenance check, not legal advice or a separate licence grant.

## Citation quotation and page targets

The three answer citations use distinct, previously verified passages on PDF page 2. Citation popovers show a short exact quotation and a thumbnail; they do not contain method explanations or additional result panels.

1. **Link caption — figure.** Quote: “A rock outcrop called Link shows signs of being formed by the deposition of water.” Source is the italic caption beneath the Link photo. Verified boxes in PDF points: `(46.080, 217.376)–(282.285, 227.600)` and `(46.080, 229.376)–(74.692, 239.600)`.
2. **Stream flow — words.** Quote: “The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.” Verified boxes: `(44.500, 584.660)–(274.202, 596.480)`, `(44.500, 600.160)–(286.421, 611.980)`, `(44.500, 615.660)–(133.780, 627.480)`. The last box ends at “site.” before “Analysis,” on the printed line.
3. **John Klein — meaning.** Short popover quote: “geological and mineralogical evidence for sustained liquid water.” Full source passage: `first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,`. Verified boxes: `(314.640, 139.400)–(559.882, 151.220)`, `(314.640, 154.900)–(547.671, 166.720)`, `(314.640, 170.400)–(560.840, 182.220)`, `(314.640, 185.900)–(423.730, 197.720)`. The final box ends at “water,” before “other key elemental ingredients.”

An additional uncited representative match uses the page-2 passage `Curiosity touched down right in an area where water once coursed vigorously over the surface.` Boxes: `(44.500, 538.160)–(289.750, 549.980)`, `(44.500, 553.660)–(269.189, 565.480)`, `(44.500, 569.160)–(279.422, 580.980)`.

### Coordinate method and limits

Poppler `pdftotext -bbox-layout -f 2 -l 2` extracted word and line boxes from the original PDF. Source boxes are expressed in the PDF page’s 612×792-point coordinate space with a top-left origin and normalized to the 1105×1430-pixel, 130 dpi Poppler page image. The overlay therefore matches the static Poppler render. It does not verify the production pdf.js coordinate conversion or demonstrate live retrieval.

The `words`, `meaning`, and `figure` labels are **simulated method labels** attached to genuine passages for this design proof. They were not returned or classified by the production retrieval pipeline. Turning on **Show matches** displays only these on-page overlays; the answer itself is unchanged.

## Fonts

Font files, exact SHA-256 values, source revisions, and local SIL Open Font License 1.1 texts are listed in `FONT-LICENSES.md`. The selected proof fonts are Newsreader for answers, Instrument Sans for interface text, and IBM Plex Mono for compact metadata. Source Serif 4 and Geist are locally bundled comparison candidates. All are served from this proof directory.
