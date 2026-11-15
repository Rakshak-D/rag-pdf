# Asset provenance

## NASA document and rendered pages

- Source PDF: `mars-science-laboratory.pdf` (Mars Science Laboratory/Curiosity factsheet), 4 pages.
- Repository source location: `data/pdf/mars-science-laboratory.pdf`, read from the protected `main` commit `7e9522618296af6a7f25652cc8161009d9a92685`; the PDF blob SHA-1 was `62296a2ce54907e76bb05859524e0184ee351539`.
- NASA source URL: <https://science.nasa.gov/wp-content/uploads/2023/09/mars-science-laboratory.pdf>
- Static page renders: `assets/pages/page-02.png`, `assets/pages/page-03.png`; contact-sheet thumbnails: `assets/pages/contact-1.png` through `contact-4.png`.
- Rendering method: Poppler `pdftoppm`; proof page renders at 130 dpi and thumbnails at 45 dpi. The PDF itself was not modified.

## Figure and claim-specific source passages

- Figure: the Link outcrop on page 2, extracted from repository source `data/images_pymupdf/mars-science-laboratory/page_2_img_0.png`; copied as `assets/figures/link-outcrop.png`.
- Original extracted-figure Git blob: dddaf8883e7b1d677569efd8c0afcea0fe9c1452.
- Figure identity: NASA/JPL-Caltech/MSSS image PIA16188, “Link to a Watery Past.” NASA's Photojournal page identifies the image and credit: <https://science.nasa.gov/photojournal/link-to-a-watery-past/>.
- Visible caption, transcribed from the page 2 factsheet: “A rock outcrop called Link shows signs of being formed by the deposition of water.”
- Displayed credit: `NASA/JPL-Caltech/MSSS`.
- Claim 1 — “The NASA caption says Link shows signs of being formed by the deposition of water.” Its source is the italic caption immediately under the Link photograph: “A rock outcrop called Link shows signs of being formed by the deposition of water.” The highlight boxes are (46.080, 217.376)–(282.285, 227.600) and (46.080, 229.376)–(74.692, 239.600) PDF points.
- Claim 2 — “The factsheet identifies rounded pebbles and hardened sand in conglomerate rocks as evidence of stream flow.” Its source is: “The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.” The three highlight boxes are (44.500, 584.660)–(274.202, 596.480), (44.500, 600.160)–(286.421, 611.980), and (44.500, 615.660)–(133.780, 627.480) PDF points. The final box ends at the word “site.” before the next unrelated word, “Analysis,” on the same printed line.
- Claim 3 — “The first drilled sample from a rock target called John Klein provided evidence of conditions favorable for life and sustained liquid water.” Its exact source passage is `first drilled sample, from a rock target called “John Klein,” provided the evidence of conditions favorable for life in Mars’ early history: geological and mineralogical evidence for sustained liquid water,`. The four highlight boxes are (314.640, 139.400)–(559.882, 151.220), (314.640, 154.900)–(547.671, 166.720), (314.640, 170.400)–(560.840, 182.220), and (314.640, 185.900)–(423.730, 197.720) PDF points. The last box ends at “water,” before the subsequent words “other key elemental ingredients.”
- Coordinate verification: Poppler `pdftotext -bbox-layout -f 2 -l 2` extracted word and line boxes from the original PDF. The selected words were matched to the rendered page 2 image; page coordinates use the PDF page’s 612×792-point bounds and top-left origin. Each box was normalized to the 1105×1430-pixel, 130 dpi Poppler page render. This validates only the static Poppler page overlay; it does not validate production PDF.js coordinate conversion.
- The three claims use separate source IDs, numbered chips (`1 · p.02`, `2 · p.02`, `3 · p.02`) and separately selectable verified boxes. Only the selected claim's passage is highlighted; each leader ends at that claim's first marked line.
- NASA media guidance: <https://www.nasa.gov/nasa-brand-center/images-and-media/>. The cited image credit is preserved next to the figure. The proof does not use NASA marks as product branding.

## Fonts

Both typefaces are bundled locally from the official Google Fonts repository at immutable commit `29b4ec6df2522a8aee279414b78a07910b065874` and include their SIL Open Font License texts in this directory.

- IBM Plex Sans variable: `assets/fonts/IBM-Plex-Sans-Variable.ttf`, SHA-256 `3B031AA4216174205BD8471F88A49B91F093169E9E87BD5262242BC5967FE2E3`.
- IBM Plex Mono regular: `assets/fonts/IBM-Plex-Mono-Regular.ttf`, SHA-256 `6A3412F058C7D8DFD9170C41E85ADE48E5156ECB89356110CA57A0A27734AF46`.
- License files: `assets/fonts/OFL-IBM-Plex-Sans.txt` and `assets/fonts/OFL-IBM-Plex-Mono.txt`.
