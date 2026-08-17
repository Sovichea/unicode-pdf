# Known limitations

This project is an early research prototype. The architecture has strong cross-script evidence, but universal reader interoperability is not solved yet.

## Arabic and reader-side BiDi behavior

The included synthetic logical-cluster PDF experiment preserves the original Arabic Unicode in `/ToUnicode` and places clusters using HarfBuzz visual geometry.

In the current pinned cross-reader matrix, Poppler `pdftotext` and PDFium still apply reader-side Arabic/BiDi/mark reconstruction and do not return the source sequence byte-for-byte. **MuPDF 1.26.12 does return the current Arabic fixture exactly**, which is strong evidence that the generated PDF contains sufficient logical Unicode and that the remaining divergence is consumer-specific.

This is a critical open interoperability problem because the production goal is correct copy/paste in ordinary third-party readers without requiring a custom reader patch.

The current writer now combines deliberate FriBidi run segmentation, logical-order content, Tagged PDF structure, MCIDs, `/Lang`, `/WritingMode`, and scoped `/ActualText`. Poppler still changes Arabic mark/order sequences after decoding them.

The automated matrix now covers Poppler, MuPDF, PDFium, and PDF.js extraction APIs. UI clipboard behavior in Acrobat, Preview, Chrome/Edge, Firefox, and mobile readers still requires release-level testing because extraction APIs are not guaranteed to match selection/copy behavior.

## PDF.js behavior

The research patch in `experiments/pdfjs-logical-text-mode.patch` documents three PDF.js behaviors discovered during testing:

- multi-codepoint `/ToUnicode` values containing combining or format characters can affect width classification;
- PDF.js performs its own BiDi transformation during text extraction;
- PDF.js can discard explicit whitespace and infer spaces geometrically.

The patch is diagnostic. Generated PDFs should not depend on a custom PDF.js fork for basic Unicode correctness.

## Font synthesis

The Rust implementation now appends real TrueType composite glyphs for shaped logical units. The current scope is intentionally narrow:

- `glyf`-based TrueType fonts only;
- one standalone font face, not TTC/OTC collections;
- horizontal shaping/metrics;
- source font retained rather than subset;
- no CFF/CFF2 output;
- no variable-font instance materialization yet.

The generated fonts have been reopened and decomposed successfully with FontTools for Khmer, Arabic, and Devanagari fixtures, but broader font-validator and renderer coverage is still required.

## Current layout and Type0 PDF emission scope

The Rust writer now emits multiple Type0/CIDFontType2 resources across multiple tagged pages. `unicode-pdf-layout` provides `cmap`-based fallback, whitespace wrapping, line placement, and pagination. The remaining production limitations are:

- horizontal writing only;
- greedy whitespace line breaking rather than full UAX #14;
- no language-aware hyphenation or justification;
- fixed font size and line height per layout call;
- source fonts are retained rather than truly subset;
- no stream compression;
- CFF/CFF2, collections, variable instances, and color-font visuals are not yet supported.

Soft line wrapping is semantic-preserving inside the producer, but mainstream extractors often synthesize newlines or replace source spaces based on visual lines. The strict conformance harness therefore marks the long wrapped fixtures as known failures even when the non-whitespace script content is visually and logically intact in the PDF model.

## Tagged PDF and `/ActualText`

Tagged PDF structure, MCIDs, paragraph/span grouping, `/Lang`, and directional `/WritingMode` are now implemented. `/ActualText` is controlled by `ActualTextPolicy`; the CLI scopes it to multi-codepoint logical units.

In the tested Poppler version, neither marked-content nor structure-element replacement text prevents later Arabic BiDi/combining-mark processing. Tags are still important for logical structure and accessibility, but they are not a universal override for reader extraction algorithms.

## Color emoji

The logical text model supports emoji/ZWJ sequences, but the production visual strategy for COLR/CPAL, SVG, CBDT/CBLC, and other color-font formats is not implemented.

## Clusterless/default-ignorable source characters

The initial logical-unit builder assumes non-empty text has shaping cluster output beginning at source byte 0. A production shaper adapter must explicitly handle source characters that produce no visible glyph while still preserving their semantics where required.

## System HarfBuzz adapter

The current production shaping adapter dynamically loads the system HarfBuzz shared library and supports Unix-like systems. A pure-Rust HarfRust backend and Windows loader are not implemented yet. The shaping API is backend-neutral so these can be added without changing the logical PDF model.
