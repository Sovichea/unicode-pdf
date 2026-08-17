# Milestone: multi-font fallback and paragraph layout

This milestone moves `unicode-pdf` from positioned development lines to a small real document layout engine.

## Implemented

- `unicode-pdf-layout` workspace crate.
- TrueType/OpenType Unicode `cmap` coverage parsing for format 4 and format 12 subtables.
- Ordered fallback selection based on actual font coverage.
- Maximal same-font shaping spans within resolved BiDi runs.
- Global UTF-8 source ranges retained after fallback segmentation and shaping.
- Greedy soft wrapping at explicit whitespace opportunities.
- Scalar-boundary fallback for oversize unbreakable segments.
- Line-level FriBidi resolution after wrap decisions.
- HarfBuzz shaping of each resolved font/direction span.
- LTR and RTL visual run placement independent from semantic operator order.
- A4 pagination with fixed margins, font size, and line height.
- Multiple synthesized TrueType fonts embedded as independent Type0/CIDFontType2 resources.
- Multi-page page tree, content streams, `/StructParents`, page-local MCIDs, `ParentTree`, and cross-page paragraph/span structure.
- Contiguous LTR CID strings when semantic and visual order coincide.
- New `emit-layout-pdf` CLI command.
- New multi-font inline, wrapped, and five-page conformance fixtures.

## Important invariants

Font fallback does not alter logical Unicode. Soft wrapping does not inject a Unicode line break. The PDF layout layer assigns only visual geometry:

```text
source paragraph
      |
      +--> font fallback + BiDi + shaping --> logical units
      |                                      (same Unicode)
      |
      +--> wrapping + pagination ----------> page/x/y only
```

The generated PDF can therefore distinguish a real source paragraph separator from a purely visual line break, even though some third-party readers still reconstruct clipboard/extraction whitespace from geometry.

## Validation

The mixed-font inline fixture uses four fallback fonts but only Latin, Khmer, and Devanagari content. It extracts exactly in:

- Poppler 25.06.0
- MuPDF 1.26.12
- PDFium 149.0.7825.0

Stock PDF.js 6.2.108 still inserts spaces inside some multi-codepoint logical CIDs, matching the previously documented PDF.js behavior.

The wrapped and multi-page fixtures intentionally remain strict known failures because readers synthesize visual line breaks and, when Arabic is present, apply their own BiDi reconstruction. The five-page PDF is structurally valid, tagged, renders correctly, and preserves source Unicode in the producer model before serialization.

## Reader-friendly LTR emission

The first multi-font prototype used one absolute `Tm`/`Tj` pair for every logical unit. PDFium interpreted even ordinary Latin letters as fragmented text. The final writer emits one contiguous CID string for LTR runs when `/ActualText` does not require per-unit scopes:

```pdf
1 0 0 1 x y Tm <000100020003...> Tj
```

RTL runs retain per-unit absolute placement because logical operator order and visual X order differ by design.

## HarfBuzz FFI regression found by the milestone

Metric discovery shapes empty text. HarfBuzz legitimately returns a zero glyph count with null array pointers. Rust 1.97's UB checks caught the adapter constructing a zero-length slice from that null pointer. The adapter now uses `&[]` whenever the returned glyph count is zero and only calls `slice::from_raw_parts` for nonzero counts.

## Remaining layout work

This is deliberately not a full typesetting engine yet. The next layout features include:

- UAX #14 line-breaking classes and pair rules;
- language-aware hyphenation;
- justification and tab stops;
- mixed font sizes/styles and baseline alignment;
- CFF/CFF2 and color-font visual support;
- real source-font subsetting;
- selection/highlight geometry conformance across line and page boundaries.
