# Asset provenance

## NASA document and rendered pages

- Source PDF: `mars-science-laboratory.pdf` (Mars Science Laboratory/Curiosity factsheet), 4 pages.
- Repository source location: `data/pdf/mars-science-laboratory.pdf`, read from the protected `main` commit `7e9522618296af6a7f25652cc8161009d9a92685`; the PDF blob SHA-1 was `62296a2ce54907e76bb05859524e0184ee351539`.
- NASA source URL: <https://science.nasa.gov/wp-content/uploads/2023/09/mars-science-laboratory.pdf>
- Static page renders: `assets/pages/page-02.png`, `assets/pages/page-03.png`; contact-sheet thumbnails: `assets/pages/contact-1.png` through `contact-4.png`.
- Rendering method: Poppler `pdftoppm`; proof page renders at 130 dpi and thumbnails at 45 dpi. The PDF itself was not modified.

## Figure and passage

- Figure: the Link outcrop on page 2, extracted from repository source `data/images_pymupdf/mars-science-laboratory/page_2_img_0.png`; copied as `assets/figures/link-outcrop.png`.
- Original extracted-figure Git blob: dddaf8883e7b1d677569efd8c0afcea0fe9c1452.
- Figure identity: NASA/JPL-Caltech/MSSS image PIA16188, “Link to a Watery Past.” NASA's Photojournal page identifies the image and credit: <https://science.nasa.gov/photojournal/link-to-a-watery-past/>.
- Visible caption, transcribed from the page 2 factsheet: “A rock outcrop called Link shows signs of being formed by the deposition of water.”
- Displayed credit: `NASA/JPL-Caltech/MSSS`.
- Highlighted passage, page 2: “The evidence for stream flow was in rounded pebbles mixed with hardened sand in conglomerate rocks at and near the landing site.”
- Highlight geometry method: `pdftotext -bbox-layout` was run against the source PDF and the passage's rendered line boxes were matched on page 2 (612×792 PDF points): (44.50, 584.66)–(274.20, 596.48), (44.50, 600.16)–(286.42, 611.98), and (44.50, 615.66)–(133.78, 627.48). These boxes were normalized onto the 130 dpi Poppler page image. This validates only this static page overlay; it does not validate production PDF.js coordinate conversion.
- NASA media guidance: <https://www.nasa.gov/nasa-brand-center/images-and-media/>. The cited image credit is preserved next to the figure. The proof does not use NASA marks as product branding.

## Fonts

Both typefaces are bundled locally from the official Google Fonts repository at immutable commit `29b4ec6df2522a8aee279414b78a07910b065874` and include their SIL Open Font License texts in this directory.

- IBM Plex Sans variable: `assets/fonts/IBM-Plex-Sans-Variable.ttf`, SHA-256 `3B031AA4216174205BD8471F88A49B91F093169E9E87BD5262242BC5967FE2E3`.
- IBM Plex Mono regular: `assets/fonts/IBM-Plex-Mono-Regular.ttf`, SHA-256 `6A3412F058C7D8DFD9170C41E85ADE48E5156ECB89356110CA57A0A27734AF46`.
- License files: `assets/fonts/OFL-IBM-Plex-Sans.txt` and `assets/fonts/OFL-IBM-Plex-Mono.txt`.