# Bundled font licence inventory

All bundled type files include their SIL Open Font License 1.1 text in `assets/fonts/`. The browser proof loads only local files; it makes no font requests to a font service.

## Fonts used by the proof

| Family and file | Use | Source revision | SHA-256 |
|---|---|---|---|
| Newsreader, `Newsreader-Variable.ttf` | Answer reading serif | Google Fonts repository commit `cc39c4c2c304bc368c9b7c311ea2bcc6013299fe` | `8A08D13F8A6C0D51BE379A60AF84F945F65369A67E509EE3C3BDCC421254D7C1` |
| Instrument Sans, `InstrumentSans-Variable.ttf` | Interface and controls | Google Fonts repository commit `cc39c4c2c304bc368c9b7c311ea2bcc6013299fe` | `B24F1812584816958AFCF22E22D08E44318C5E51651E25D2438EFDDE389B33B1` |
| IBM Plex Mono, `IBM-Plex-Mono-Regular.ttf` | Page numbers, source labels, compact metadata | Google Fonts repository commit `29b4ec6df2522a8aee279414b78a07910b065874` | `6A3412F058C7D8DFD9170C41E85ADE48E5156ECB89356110CA57A0A27734AF46` |

## Comparison candidates

| Family and file | Use in comparison | Source revision | Licence | SHA-256 |
|---|---|---|---|---|
| Source Serif 4, `SourceSerif4-Variable.ttf` | Reading-serif comparison | Google Fonts repository commit `cc39c4c2c304bc368c9b7c311ea2bcc6013299fe` | SIL OFL 1.1; `OFL-SourceSerif4.txt` | `97B2D4DA6E3CB494B5A1E66AE176914D852CCABEF49E0C02C0DF25F3E39ACA0B` |
| Geist, `Geist-Variable.ttf` | Interface-sans comparison | Google Fonts repository commit `cc39c4c2c304bc368c9b7c311ea2bcc6013299fe` | SIL OFL 1.1; `OFL-Geist.txt` | `73894E0448CAE90A92B6C2F8732B7BB9ACB7B94C418BFF559DAD4A18E1DE9659` |

IBM Plex Sans is retained from the earlier proof bundle for provenance and has not been chosen for this composition. Its file hash is `3B031AA4216174205BD8471F88A49B91F093169E9E87BD5262242BC5967FE2E3`; the bundled licence is `OFL-IBM-Plex-Sans.txt`.

## Redistribution notes

The source repository identifies these families as SIL Open Font License 1.1 and each bundled font has a corresponding local licence text. The font comparison was rendered using the bundled files; the CSS does not silently fall back to a system font for the measured specimens. The chosen proof pair is Newsreader and Instrument Sans, with IBM Plex Mono for metadata.
