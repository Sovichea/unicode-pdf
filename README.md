# unicode-pdf

`unicode-pdf` is an open-source experiment and emerging Rust library for producing PDFs whose visible text and copied/extracted Unicode stay correct for complex scripts.

The project starts from a simple rule:

> Preserve the original logical Unicode through shaping. Never reconstruct source text from final glyph IDs.

This repository grew out of reproducible failures involving Khmer PDF copy/paste, then validation across Arabic, Devanagari, emoji/ZWJ sequences, and mixed LTR/RTL text.

## Status

**Early alpha / research prototype.** The Rust workspace now includes backend-neutral shaping and BiDi traits, runtime-loaded system HarfBuzz and FriBidi adapters, CID planning, authoritative `/ToUnicode`, TrueType composite-glyph synthesis, Type0/CIDFontType2 PDF emission, Tagged PDF semantic structure, and compiler-owned logical paragraphs that remain independent from visual line/page breaks.

The `emit-pdf` development command performs `UTF-8 -> FriBidi directional spans -> HarfBuzz logical units -> synthetic glyph font -> tagged Type0 PDF`. The newer `emit-layout-pdf` path adds real `cmap`-based multi-font fallback, paragraph wrapping, multi-line layout, pagination, multiple embedded Type0 fonts, and page-local tagged-PDF MCIDs. A cross-reader conformance harness exercises generated PDFs through Poppler, MuPDF, PDFium, and stock PDF.js. Khmer and Devanagari remain exact in Poppler, MuPDF, and PDFium for the established single-font fixtures. Multi-font inline Khmer/Latin/Devanagari now passes exactly in Poppler, MuPDF, and PDFium. For Typsastra's PDF.js-based live preview, the repository now also includes an opt-in `preserveLogicalText` compatibility patch and a real-browser DOM Range/Selection conformance harness. Arabic and soft-wrapped paragraph extraction still expose reader-specific reconstruction behavior.

## Goals

- Preserve exact logical Unicode for copy/paste and extraction.
- Render complex shaping correctly with HarfBuzz-compatible cluster information.
- Keep logical text order separate from visual glyph geometry.
- Use normal PDF `/ToUnicode` mappings as the primary extraction mechanism.
- Use `/ActualText` and Tagged PDF semantics as complementary mechanisms, not as a substitute for correct `/ToUnicode`.
- Work across Khmer, Arabic, Indic scripts, Myanmar, Thai/Lao, Hebrew, emoji, Latin ligatures, CJK, and mixed BiDi content.
- Remain useful outside any one editor or typesetter.
- Allow optional source-span metadata for editor preview synchronization without coupling the PDF text model to a specific application.

## Core model

The central abstraction is `LogicalPdfUnit`:

```rust
pub struct LogicalPdfUnit {
    pub unicode: String,
    pub source_range: Option<SourceRange>,
    pub font_id: FontId,
    pub glyphs: Vec<PositionedGlyph>,
}
```

A unit represents one logical text unit and the glyphs that visually render it.

Examples:

| Logical Unicode | Visual representation |
|---|---|
| `A` | one ordinary Latin glyph |
| `ffi` | one ligature glyph |
| `ខ្ញុំ` | multiple shaped Khmer glyph components |
| `क्षे` | shaped Devanagari conjunct/components |
| Arabic `ب` | a contextual Arabic glyph form |
| `👨‍👩‍👧‍👦` | one emoji glyph or another visual representation |

The same Unicode string may map to different visual CIDs when contextual shaping differs. For example, Arabic initial, medial, and final forms can share the same `/ToUnicode` value while using different visual glyph sequences.

## Architecture

```text
Original logical Unicode
          |
          v
   BiDi / script analysis
          |
          v
       HarfBuzz
          |
          +---------------------------+
          |                           |
          v                           v
 logical source order           visual glyph geometry
          |                           |
          v                           v
    LogicalPdfUnit              positioned glyphs
          |                           |
          +-------------+-------------+
                        |
                        v
                 PDF text planner
                        |
              +---------+----------+
              |                    |
              v                    v
        ordinary CID         synthetic logical CID
              |                    |
              +---------+----------+
                        |
                        v
               authoritative /ToUnicode
                        |
                        v
                   logical text
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design in detail.

## Workspace

- `unicode-pdf-core`: logical text units, source spans, shaping records, and logical reconstruction.
- `unicode-pdf-bidi`: safe backend-neutral Unicode BiDi analysis contract.
- `unicode-pdf-bidi-fribidi`: isolated runtime-loaded GNU FriBidi adapter.
- `unicode-pdf-font`: TrueType Unicode `cmap` coverage, visual-unit keys, CID allocation, and composite-glyph synthesis.
- `unicode-pdf-layout`: `cmap`-driven font fallback, compiler-owned logical paragraphs, external language-aware break opportunities, BiDi-aware line wrapping, visual line placement, and pagination.
- `unicode-pdf-shape`: safe backend-neutral shaping trait and result types.
- `unicode-pdf-shape-harfbuzz`: isolated runtime-loaded system HarfBuzz adapter.
- `unicode-pdf-write`: `/ToUnicode`, single- and multi-font Type0/CIDFontType2 embedding, multi-page resources, `/CIDToGIDMap`, Tagged PDF structure, page-local MCIDs, `/Lang`, writing direction metadata, and logical-order page-content emission.
- `unicode-pdf-cli`: inspection, shaping, synthetic-font validation, and end-to-end PDF generation commands.
- `fixtures`: source strings used for conformance tests.
- `experiments`: proof-of-concept synthetic font/PDF generator and PDF.js investigation artifacts.
- `conformance`: cross-reader extraction, selection geometry, and real-browser PDF.js TextLayer/Range harnesses.
- `integrations/pdfjs`: the opt-in Typsastra `preserveLogicalText` patch for `pdfjs-dist` 6.2.108 and integration notes.

## Build

### Requirements

- A stable Rust toolchain with Cargo.
- System HarfBuzz and FriBidi shared libraries on Unix-like systems to use the current shaping and BiDi adapters.
- A `glyf`-based TrueType font for the current Rust composite synthesizer.
- Python 3.10+ and FontTools only if you want to run or inspect the legacy proof-of-concept experiment.

Install Rust using your platform's standard Rust installation method, then:

```bash
cargo build --workspace
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

The initial source release is verified with Rust 1.97.1 using all three commands above plus `cargo fmt --check`.

Run the inspection CLI:

```bash
cargo run -p unicode-pdf-cli -- inspect fixtures/khmer.txt
```

Shape a fixture with the Rust HarfBuzz adapter:

```bash
cargo run -p unicode-pdf-cli -- shape \
  /path/to/NotoSansKhmer-Regular.ttf \
  fixtures/khmer.txt
```

Build a standalone TrueType font with appended logical composite glyphs:

```bash
cargo run -p unicode-pdf-cli -- synthesize-font \
  /path/to/NotoSansKhmer-Regular.ttf \
  fixtures/khmer.txt \
  /tmp/khmer-logical.ttf
```

The synthesizer retains the source font and appends reusable composite glyphs keyed by logical Unicode plus shaped visual form. TrueType subsetting is not implemented yet.

Generate a complete PDF using the Rust Type0/CIDFontType2 path:

```bash
cargo run -p unicode-pdf-cli -- emit-pdf \
  /path/to/NotoSansKhmer-Regular.ttf \
  fixtures/khmer.txt \
  /tmp/khmer.pdf
```

The emitted font uses `/Encoding /Identity-H`, an explicit binary `/CIDToGIDMap`, CID widths, an embedded synthesized TrueType font, and an authoritative `/ToUnicode` CMap. FriBidi resolves directional spans before shaping. Text operators stay in logical source order while each span is placed in visual BiDi order. The page also contains a `StructTreeRoot`, paragraph/span MCIDs, `/Lang`, and writing-mode metadata.

Generate a wrapped, paginated document with ordered font fallback:

```bash
cargo run -p unicode-pdf-cli -- emit-layout-pdf \
  fixtures/multifont-layout.txt \
  /tmp/multifont.pdf \
  /path/to/NotoSans-Regular.ttf \
  /path/to/NotoSansKhmer-Regular.ttf \
  /path/to/NotoSansArabic-Regular.ttf \
  /path/to/NotoSansDevanagari-Regular.ttf
```

Fallback selection uses each font's Unicode `cmap`, not filename/script assumptions. The first font covering a scalar wins, while neutral characters preferentially stay with the surrounding font when possible. Soft wrapping changes geometry only; the logical units do not receive inserted line-break characters.

Language-aware line breaking can be supplied as UTF-8 byte offsets without modifying the source string:

```bash
cargo run -p unicode-pdf-cli -- emit-layout-pdf-breaks \
  fixtures/khmer-paragraph-natural.txt \
  fixtures/khmer-paragraph-natural.breaks.txt \
  /tmp/khmer-paragraph.pdf \
  /path/to/NotoSansKhmer-Regular.ttf
```

The layout result retains the exact pre-layout paragraph text and hard source paragraph boundaries. The tagged PDF hierarchy is `StructTreeRoot -> Document -> P -> Span -> MCID`. By default each `/P` structure element also carries the exact compiler-owned paragraph Unicode in `/ActualText`. Experimental page-fragment `/ActualText` policies are available for interoperability research but are not the production default because reader support is inconsistent.

On a Debian/Ubuntu-like development system with Noto fonts, Poppler, and Ghostscript installed, run the interoperability smoke test with:

```bash
scripts/validate-system-fonts.sh
```

Run the cross-reader Unicode conformance harness with all locally available readers:

```bash
scripts/run-conformance.sh
```

Include stock PDF.js by pointing at a PDF.js distribution:

```bash
scripts/run-conformance.sh --pdfjs-dist /path/to/pdfjs-dist
```

The harness can also protect known-good reader/script combinations against regressions:

```bash
scripts/run-conformance.sh --check-baseline
```

See [conformance/README.md](conformance/README.md) and [the cross-reader milestone report](docs/MILESTONE_CROSS_READER_CONFORMANCE.md).

Paragraph semantics and external Khmer break opportunities are covered in [the semantic paragraph fidelity milestone](docs/MILESTONE_SEMANTIC_PARAGRAPH_FIDELITY.md).

### Typsastra PDF.js selection mode

Typsastra can keep its existing PDF.js preview architecture by using the included opt-in logical-text mode:

```bash
python integrations/pdfjs/apply_typsastra_patch.py \
  --input node_modules/pdfjs-dist \
  --output vendor/pdfjs-dist-typsastra
```

Then feed the text layer with:

```js
page.streamTextContent({
  includeMarkedContent: true,
  disableNormalization: true,
  preserveLogicalText: true,
});
```

The compatibility harness tests actual browser `Range`/`Selection` geometry rather than only `getTextContent()`:

```bash
python conformance/run_pdfjs_typsastra.py \
  --pdfjs-dist /path/to/pdfjs-dist \
  --browser chromium \
  --check-baseline
```

See [integrations/pdfjs/README.md](integrations/pdfjs/README.md) and [the PDF.js compatibility milestone](docs/MILESTONE_PDFJS_TYPSASTRA_COMPATIBILITY.md).

## Run the proof-of-concept experiment

The experiment demonstrates the key production idea with real HarfBuzz shaping and synthetic composite TrueType glyphs.

Create a Python virtual environment and install FontTools:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install fonttools
```

Then run:

```bash
python experiments/synthetic_cluster_pdf.py \
  --font /path/to/NotoSansKhmer-Regular.ttf \
  --text "កម្ពុជា ខ្ញុំសរសេរភាសាខ្មែរ" \
  --out experiments/out/khmer.pdf
```

For Arabic:

```bash
python experiments/synthetic_cluster_pdf.py \
  --font /path/to/NotoSansArabic-Regular.ttf \
  --text "العَرَبِيَّة لا مُحَمَّد" \
  --out experiments/out/arabic.pdf
```

The script uses the system HarfBuzz library through `ctypes`, shapes the complete run, groups glyphs by source cluster, creates composite glyphs, writes a CIDFontType2 subset, and generates `/ToUnicode` directly from the original logical text.

See [experiments/README.md](experiments/README.md).

## Unicode preservation contract

The project treats extraction correctness as a conformance requirement, not a best-effort feature.

For logical input `S`, extraction should return `S` exactly unless the caller has explicitly requested a documented semantic transformation.

In particular:

- shaping must not change copied text;
- ligatures must copy as their original Unicode sequence;
- BiDi rendering must not reverse logical source order;
- font fallback must not change copied text;
- visual line wrapping must not invent semantic whitespace;
- automatic hyphenation must not inject a copied hyphen unless semantically present;
- combining marks and ZWJ sequences must survive intact.

See [docs/UNICODE_CONTRACT.md](docs/UNICODE_CONTRACT.md).

Current reader-specific gaps are tracked in [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md). The latest implementation report is [docs/MILESTONE_MULTIFONT_PARAGRAPH_LAYOUT.md](docs/MILESTONE_MULTIFONT_PARAGRAPH_LAYOUT.md).

## Interoperability target

The intended conformance matrix includes:

- Adobe Acrobat / Reader
- Chrome and Chromium PDF viewers
- Firefox / PDF.js
- Poppler `pdftotext`
- MuPDF
- macOS Preview
- mobile system PDF viewers

The generated PDF should be standards-oriented and should not require a custom viewer for correct copy/paste. This is the target, not yet a claim of universal interoperability. In particular, Arabic extraction still exposes reader-side BiDi/mark-reordering differences in the current prototype.

Generated PDFs remain standards-oriented and do not require a custom viewer for their PDF semantics. Typsastra nevertheless has a product-specific requirement to make browser selection follow those semantics in its existing PDF.js preview. The repository therefore carries an opt-in PDF.js compatibility mode for compiler-authored logical-CID PDFs; normal PDF.js behavior stays the default for arbitrary documents.

## Roadmap

1. Stabilize `LogicalPdfUnit` and cluster reconstruction invariants. **Implemented.**
2. Add a Rust HarfBuzz shaping backend behind a trait. **Implemented for system HarfBuzz on Unix.**
3. Implement ordinary-CID versus synthetic-CID planning. **CID reuse and contextual visual keys implemented; ordinary-CID optimization remains.**
4. Implement TrueType composite glyph synthesis and subsetting. **Composite synthesis implemented for `glyf` TrueType fonts; subsetting remains.**
5. Emit complete Type0/CIDFontType2 PDF text runs. **Implemented for single- and multi-font horizontal documents.**
6. Add semantic run segmentation, `/ActualText`, MCIDs, `/Lang`, and a Tagged PDF structure tree. **Implemented.**
7. Add cross-reader extraction conformance tests. **Implemented.**
8. Add `cmap`-based font fallback, paragraph wrapping, and pagination. **Implemented.**
9. Add selection/highlight geometry tests for mixed BiDi and wrapped multi-font content. **Implemented.**
10. Add Typsastra PDF.js logical-text mode and browser DOM selection conformance. **Implemented for PDF.js 6.2.108; Chromium validated, Firefox CI/manual validation pending.**
11. Add optional source-span sidecar/index support for editors and typesetters.
12. Add real font subsetting, CFF/CFF2/color-font support, and Unicode line-breaking/hyphenation.
13. Integrate the engine into real PDF-producing applications.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

The most valuable early contributions are small reproducible PDFs, script-specific shaping cases, reader interoperability results, and improvements to the logical/visual text model.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
