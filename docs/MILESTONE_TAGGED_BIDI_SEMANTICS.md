# Milestone: BiDi segmentation and Tagged PDF semantics

This milestone moves `unicode-pdf` from one shaped run per source line to an explicit semantic model for bidirectional text and tagged PDF output.

## Implemented

### Runtime-loaded FriBidi adapter

Two new crates separate the Unicode Bidirectional Algorithm from PDF writing:

- `unicode-pdf-bidi`: safe backend-neutral `BidiResolver` interface;
- `unicode-pdf-bidi-fribidi`: Unix runtime adapter for GNU FriBidi.

The adapter converts UTF-8 to UTF-32, asks FriBidi for resolved embedding levels and logical-to-visual positions, and returns contiguous directional runs in logical source order. Each run records:

- its UTF-8 source range;
- resolved embedding level;
- LTR/RTL direction;
- visual run rank.

This keeps source order and page order separate.

### Direction-aware shaping

`ShapeOptions` now accepts an explicit resolved direction. The HarfBuzz adapter loads `hb_buffer_set_direction` and applies the FriBidi result before asking HarfBuzz to infer the remaining segment properties.

This prevents a run beginning with neutral or numeric characters from being shaped using an accidental direction guess.

### Mixed-BiDi visual layout

The development CLI now:

1. resolves each logical line with FriBidi;
2. shapes each directional span independently;
3. keeps the resulting plans in logical source order;
4. sorts only their page placement by FriBidi visual rank;
5. places each span using its HarfBuzz visual bounds.

The PDF content sequence therefore remains semantic while the page coordinates follow the resolved BiDi layout.

### Tagged PDF

The Type0 writer now emits a real logical structure hierarchy:

```text
StructTreeRoot
  P
    Span -> MCID 0
    Span -> MCID 1
    ...
```

The page has `/StructParents` and `/Tabs /S`, the catalog has `/MarkInfo << /Marked true >>` and document `/Lang`, and the parent tree maps MCIDs back to their `Span` structure elements.

Each semantic span can carry:

- a BCP 47 `/Lang` value;
- `/WritingMode /LrTb` or `/RlTb` based on its resolved direction.

PDF.js `getStructTree()` recognizes the generated hierarchy and returns the expected paragraph/span structure and language tags.

### Scoped `/ActualText`

`Type0PdfOptions` now exposes an `ActualTextPolicy`:

- `Off`;
- `ComplexUnits`;
- `RtlRuns`;
- `AllRuns`.

The CLI uses `ComplexUnits`, so replacement text is added only around logical units that contain multiple Unicode scalars. `/ToUnicode` remains the primary Unicode mapping.

## Validation

Generated PDFs are recognized by Poppler as `Tagged: yes`, pass Ghostscript structural rendering, and were rendered at 170 DPI for visual inspection. PDF.js `getStructTree()` also recognizes the generated paragraph/span hierarchy and per-span language tags.

With page-break/control cleanup applied to Poppler output:

| Fixture | Visual render | Poppler logical Unicode |
|---|---|---|
| Khmer | correct | exact |
| Devanagari | correct | exact |
| Arabic | correct | reader reorders marks/BiDi |
| mixed LTR/RTL | correct with a font covering both scripts | reader geometry/BiDi order differs |

The final Rust gate passes `cargo fmt --check`, `cargo build --workspace`, `cargo clippy --workspace --all-targets -- -D warnings`, and 20 unit tests with system HarfBuzz and FriBidi forced on.

The Arabic source is still not recovered byte-for-byte by Poppler. Adding structure-level or marked-content `/ActualText` does not change that behavior in the tested Poppler version. Experiments with visual-order show strings and `/ReversedChars` also did not eliminate Poppler's later Unicode BiDi processing.

This is now treated as an interoperability issue at the consumer layer rather than a missing logical mapping in the generated PDF.

## Important architecture result

The compiler now has three independently preserved layers:

```text
logical source order
        |
        +--> FriBidi semantic spans and structure-tree order
        |
        +--> HarfBuzz visual glyph geometry
        |
        +--> exact /ToUnicode and optional /ActualText
```

A PDF reader no longer needs to infer the intended logical structure from glyph coordinates alone.

## Next milestone

The next production milestone should focus on **reader interoperability and copy-order conformance**, not more glyph clustering:

1. build a cross-reader conformance harness for Acrobat/PDFium/PDF.js/Poppler/MuPDF;
2. automate exact code-point comparison for selection/copy where the reader API permits it;
3. investigate RTL marked-content/show-string strategies against current ISO 32000-2 errata and reader implementations;
4. test Tagged PDF 2.0 direction/structure semantics where supported;
5. add multi-font fallback so mixed-script paragraphs do not require a single pan-Unicode font;
6. replace the development single-line placement with paragraph layout and wrapping.
