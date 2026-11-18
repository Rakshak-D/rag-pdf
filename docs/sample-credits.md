# Bundled Reading Desk sample

The first-run sample preview uses a byte-for-byte copy of `data/pdf/mars-science-laboratory.pdf` at `frontend/public/reading-desk/mars-science-laboratory.pdf`.

- Source: NASA Mars Science Laboratory/Curiosity factsheet, <https://science.nasa.gov/wp-content/uploads/2023/09/mars-science-laboratory.pdf>.
- SHA-256: `2baee40744fc1c35b3ad015f8672a45710ac6bd9124a4041cfc0d7bfba4e10f6`.
- The preview renders page 1 from the original PDF with PDF.js. Its lime mark is placed from a unique match for the actual page text “Curiosity landed successfully”; if the text cannot be located, no mark is shown.
- The Link outcrop image is extracted from the factsheet's page 2 image asset at `data/images_pymupdf/mars-science-laboratory/page_2_img_0.png`. Its source caption is “A rock outcrop called Link shows signs of being formed by the deposition of water.” The adjacent credit is `NASA/JPL-Caltech/MSSS`; the NASA Photojournal entry is <https://science.nasa.gov/photojournal/link-to-a-watery-past/>.
- The sample document remains separate from a user's Library and is not imported, indexed, or sent to the answer service automatically.
- NASA source material is identified as NASA. No NASA insignia is used as product branding.

The interface fonts in `frontend/public/reading-desk/fonts/` are Newsreader, Instrument Sans, and IBM Plex Mono. Their corresponding SIL Open Font License notices are bundled beside each font.
