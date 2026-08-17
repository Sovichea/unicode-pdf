# Changelog

All notable changes to this project will be documented here.

## Unreleased

- Added first-class compiler-owned logical paragraphs with exact UTF-8 source ranges and hard-newline reconstruction independent from soft visual wrapping.
- Added external UTF-8 byte-offset line-break opportunities so ICU4X, Khmer segmentation, or another boundary provider can drive wrapping without inserting Unicode into source text.
- Changed Tagged PDF hierarchy to `StructTreeRoot -> Document -> P -> Span -> MCID` and added exact paragraph `/ActualText` on `/P` structure elements by default for layout PDFs.
- Added experimental page-local paragraph-fragment `/ActualText` policy for reader interoperability research; it is not the default because PDFium, MuPDF, and Poppler interpret it differently.
- Added natural one-page and multi-page Khmer paragraph conformance fixtures and a PDF-level semantic checker that verifies paragraph `/ActualText` is byte-for-byte equivalent to pre-layout source Unicode.
- Fixed cross-reader conformance adapters so physical page transitions use form-feed transport separators rather than inventing semantic newlines.
- Added `unicode-pdf-layout` with real `cmap`-based ordered font fallback, BiDi-aware line shaping, greedy whitespace wrapping, and pagination.
- Added multi-font/multi-page Type0/CIDFontType2 emission with page-local MCIDs and cross-page Tagged PDF structure.
- Added contiguous LTR CID text emission to avoid PDFium fragmentation caused by one absolute `Tm` per logical unit.
- Added inline, wrapped, and five-page mixed-font conformance fixtures.
- Fixed the system HarfBuzz adapter so zero-glyph shaping results never construct a Rust slice from a null pointer.
- Added a cross-reader Unicode conformance harness for Poppler, MuPDF, PDFium, and PDF.js with machine-readable code-point diagnostics.
- Added a regression baseline that protects currently exact reader/script combinations while keeping known interoperability failures visible.
- Added a manual clipboard comparison tool and Acrobat/Preview/browser release checklist.
- Added a pinned GitHub Actions conformance job and report artifacts.
- Confirmed that MuPDF 1.26.12 extracts the current Arabic fixture exactly from the same PDF that Poppler, PDFium, and stock PDF.js alter.
- Added compiler-side source-to-selection geometry indexing and cross-reader geometry baselines.
- Added an opt-in Typsastra `preserveLogicalText` patch for `pdfjs-dist` 6.2.108 plus real-browser DOM Range/Selection conformance tests.
- Fixed PDF.js logical-mode width accounting by preserving explicit whitespace, avoiding a second BiDi reorder, isolating multi-codepoint logical CIDs, and scaling TextLayer items to their exact PDF widths.

### Added

- Backend-neutral `unicode-pdf-shape` crate.
- Runtime-loaded Unix system HarfBuzz adapter with isolated FFI.
- Rust shaping path that preserves UTF-8 source cluster offsets for Khmer, Arabic, and Devanagari smoke tests.
- TrueType `glyf` composite-glyph synthesis in `unicode-pdf-font`.
- sfnt rebuilding for `glyf`, `loca`, `hmtx`, `hhea`, `maxp`, `head`, and checksums.
- CLI `shape`, `synthesize-font`, and end-to-end `emit-pdf` commands.
- Complete one-page Type0/CIDFontType2 writer with embedded `FontFile2`, `/CIDToGIDMap`, CID widths, and authoritative `/ToUnicode`.
- Absolute visual placement of logical-order CID operators.
- System-font interoperability smoke-test script for Khmer, Arabic, Devanagari, and mixed BiDi.
- Backend-neutral BiDi analysis and a runtime-loaded GNU FriBidi adapter.
- Tagged PDF `StructTreeRoot`, paragraph/span structure, MCIDs, parent tree, `/Lang`, and directional `/WritingMode`.
- Configurable `ActualTextPolicy` with complex-unit, RTL-run, and all-run scopes.

### Changed

- Visual CID keys now preserve baseline-relative Y placement and include logical-unit advance width.
- Workspace unsafe-code policy changed from `forbid` to `deny` so runtime HarfBuzz/FriBidi adapters can explicitly isolate audited FFI while other crates remain safe by default.
- Mixed-direction paragraphs are segmented before shaping; logical run order is preserved independently from visual placement.

## 0.1.0-alpha.1 - 2026-08-16

### Added

- Initial open-source workspace.
- `LogicalPdfUnit` and logical reconstruction model.
- Cluster grouping that restores logical source order from visually ordered shaping output.
- CID allocation keyed by logical Unicode plus shaped visual components.
- `/ToUnicode` CMap generation for multi-codepoint mappings.
- Khmer, Arabic, Devanagari, emoji, and mixed-BiDi seed fixtures.
- Synthetic cluster glyph PDF proof-of-concept.
- PDF.js interoperability investigation patch and validation notes.
- Apache-2.0 license and contribution guide.
