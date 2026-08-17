# Architecture

## 1. Problem statement

Complex-script PDF failures frequently happen because a PDF producer shapes logical Unicode into glyphs and later tries to infer Unicode from those final glyphs.

That reverse mapping is not reliable.

A shaping engine can:

- merge several code points into one glyph;
- decompose one logical cluster into several glyphs;
- reorder marks;
- substitute contextual forms;
- select the same visible glyph for different logical input;
- select different glyphs for the same logical character in different contexts.

Therefore the PDF writer must preserve logical Unicode before shaping information is discarded.

## 2. Invariants

The implementation should preserve these invariants:

1. Original logical Unicode is authoritative.
2. `/ToUnicode` is generated from preserved logical text, never by reverse-looking-up final glyph IDs.
3. Logical text order and visual glyph geometry are represented independently.
4. A reusable visual CID is keyed by both semantics and its shaped visual representation.
5. Multiple CIDs may map to the same Unicode text.
6. Source spans are optional metadata and are not required for PDF interoperability.
7. Explicit semantic whitespace is represented explicitly.

## 3. Core records

`ShapedGlyph` records the output of a shaping engine, including the source cluster byte offset and the glyph's visual position in the shaped run.

`LogicalPdfUnit` groups all glyphs originating from the same logical source span.

For RTL text, HarfBuzz may return glyphs in visual order while source cluster offsets run in the opposite direction. The logical-unit builder reconstructs source spans by sorting the distinct cluster boundaries, not by trusting glyph array order.

## 4. CID allocation

A simple unit can use an ordinary CID that points to an existing font glyph.

A complex unit can use a synthetic glyph that visually combines the shaped components. The current Rust synthesizer appends TrueType composite glyphs to `glyf`-based fonts, rewrites `loca`/`hmtx`/`hhea`/`maxp`, converts `post` to format 3 when possible, and rebuilds sfnt checksums.

CID reuse must account for contextual shaping. Two Arabic `ب` units can have the same Unicode mapping while using different visual forms, so they may require different CIDs.

Conceptually:

```text
visual key = (
  unicode,
  font identity,
  glyph IDs,
  relative component positions,
  advances
)
```

## 5. BiDi analysis and PDF text order

The `bidi` module resolves directional spans before shaping. The default public-crate configuration uses the pure-Rust `unicode-bidi` backend. An optional runtime-loaded FriBidi adapter is retained for reference/conformance testing. Both adapters return UTF-8 source ranges, embedding levels, directions, and visual span ranks while keeping the span array in logical source order.

Each span is then shaped with an explicit HarfBuzz direction. Semantic text operators follow logical source order, while span placement follows the resolved visual rank and HarfBuzz geometry.

For mixed BiDi text these orders can differ significantly. The writer never treats drawing sequence as the only source of semantic ordering.

## 6. `/ToUnicode`

Every emitted text CID gets an authoritative mapping to the exact logical Unicode represented by that CID.

A mapping can contain more than one Unicode scalar. For example, a logical CID may map to a ligature source sequence, a Khmer cluster, a Devanagari conjunct source sequence, or an emoji ZWJ sequence.

The destination strings are encoded as UTF-16BE in the CMap.

## 7. Type0/CIDFontType2 emission

The Rust writer now embeds the synthesized TrueType font behind a Type0 font with an Identity-H encoding and CIDFontType2 descendant. It emits:

- an embedded `FontFile2` stream;
- `/CIDToGIDMap` from logical CID to the appended synthetic TrueType glyph ID;
- `/W` widths scaled to PDF 1000-unit text space;
- authoritative `/ToUnicode` from preserved logical Unicode;
- logical-order text operators; contiguous CID strings are used for reader-friendly LTR spans, while absolute `Tm` placement is retained where visual order differs from semantic order.

For RTL runs this means PDF operator order can remain source-logical while the glyphs still appear at HarfBuzz-computed visual positions. The two orderings are intentionally independent.

The writer now supports multiple embedded Type0/CIDFontType2 resources and multiple tagged pages. Page-local MCIDs restart from zero on each page, while the parent tree maps each page's `/StructParents` key to its marked-content spans. LTR runs whose logical and visual order coincide are emitted as contiguous CID strings to improve reader interoperability; RTL runs retain logical operator order with absolute visual placement. Compression and true base-font subsetting remain future work.

## 8. `/ActualText` and Tagged PDF

The writer now emits `StructTreeRoot -> Document -> P -> Span` structure, MCIDs, a page parent tree, catalog/page tagging metadata, BCP 47 `/Lang`, and `/WritingMode` on directional spans. Directional spans and visual lines that share a paragraph identifier remain children of the same `/P` element.

The layout layer retains a `LogicalParagraph` containing the exact source Unicode before visual wrapping. The writer can attach that authoritative string to the `/P` structure element with `/ActualText`. Soft line wraps and physical page transitions are therefore absent from the compiler-owned paragraph representation even if a third-party extractor independently reconstructs newline characters from page geometry.

`/ActualText` is intentionally policy-controlled. Run-level replacement can be disabled, emitted only for multi-codepoint logical units, or scoped to RTL/all runs. Paragraph-level replacement is controlled separately. Structure-element paragraph `/ActualText` is the production default for layout PDFs; page-local paragraph-fragment replacement remains experimental because Poppler, MuPDF, PDFium, and PDF.js do not interpret it consistently.

These mechanisms complement correct `/ToUnicode`; they do not replace it. Current Poppler testing shows that even correct tags and replacement text do not prevent every reader from applying its own Arabic BiDi/mark transformation after decoding Unicode.

## 9. Source synchronization

Editor integration is optional.

A producer can attach source spans to `LogicalPdfUnit` records and emit a sidecar index:

```text
(page, x, y, width, height) <-> source byte range
```

This metadata is separate from PDF text extraction and should not be required by third-party PDF readers.


## 10. BiDi and shaping backend boundaries

The public `unicode-pdf` crate defines the safe `BidiResolver` contract. The default backend is pure Rust through `unicode-bidi`. The optional `system-fribidi` feature dynamically loads GNU FriBidi on Unix and keeps the FFI isolated inside the `bidi` module.

The visual run rank is used only for page placement. The run list itself remains in source-logical order.

### Shaping

The public crate defines the safe `TextShaper` contract. The default backend is HarfRust 0.13, so normal consumers do not need native shaping libraries. The optional `system-harfbuzz` feature dynamically loads the system HarfBuzz shared library on Unix and is used as a reference backend in conformance testing.

Both adapters shape an in-memory font, retain UTF-8 byte cluster offsets, accumulate visual pen positions, then convert the result into `LogicalPdfUnit` records in source order. `Document::finish_with` also lets advanced applications provide their own shaper and BiDi resolver without changing the PDF model.

Producers that already shape text can use `logical_units_from_external_glyphs`
instead. Each external glyph supplies its exact UTF-8 source range and
positioned geometry. Identical ranges become one logical unit, while uncovered
source intervals become zero-glyph units so default-ignorable or otherwise
glyph-less text remains part of the semantic string. Partially overlapping
ranges are rejected because they do not define unambiguous Unicode ownership.
The resulting units are sorted by source range; their glyph positions remain in
the producer's visual coordinate system.

## 11. Current TrueType synthesis scope

The Rust synthesizer currently targets horizontal `glyf`-based TrueType fonts. It appends one reusable composite glyph per unique CID entry and keeps the original component glyphs. It does not yet subset unused base glyphs, support CFF/CFF2 outlines, TrueType collections, variable-font instance materialization, or vertical writing.
## 12. Font fallback and paragraph layout

The `layout` module is the built-in document layout layer. It parses Unicode coverage from each candidate font's TrueType/OpenType `cmap` table, chooses the first covering font, and shapes maximal same-font spans rather than guessing coverage from font names. Neutral whitespace/punctuation prefers the surrounding font when that font covers it.

Paragraphs are retained as first-class semantic objects with exact source byte ranges and Unicode. Visual wrapping consumes break opportunities separately from that text. Default whitespace opportunities are available for space-delimited scripts, while callers can supply UTF-8 byte offsets from ICU4X, a Khmer segmenter, or another language-aware boundary provider. Oversize unbreakable segments currently fall back to Unicode scalar boundaries. Each final visual line is then resolved with FriBidi and shaped with HarfBuzz, so line-level BiDi ordering is computed after the wrap decision. Layout records retain global UTF-8 source ranges.

Soft wrapping does not add Unicode to `LogicalPdfUnit` or `LogicalParagraph`; it only assigns page, baseline, and X geometry. `LayoutDocument::logical_text()` reconstructs hard source paragraph delimiters but never visual wraps or page boundaries. This is a deliberate semantic invariant even though several third-party extractors still synthesize newline characters from visual line geometry.

Pagination uses fixed page metrics and line height in the current milestone. Paragraph shaping, fallback, and tagging can cross physical page boundaries. Full UAX #14 line breaking, language-aware hyphenation, justification, vertical text, and advanced paragraph spacing are not implemented yet.
