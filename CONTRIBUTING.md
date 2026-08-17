# Contributing to unicode-pdf

Thank you for helping improve Unicode correctness in PDF.

## Project principle

The project treats the original logical Unicode as authoritative. Visual glyphs are a rendering result and must never become the source of truth for copied or extracted text.

A proposed change should preserve that distinction.

## Getting started

1. Fork the repository and create a focused branch.
2. Install a stable Rust toolchain.
3. Run the default pure-Rust workspace tests before making changes:

   ```bash
   cargo test --workspace
   ```

   On Unix, changes touching shaping or BiDi should also be compared against the optional native reference backends:

   ```bash
   cargo test --workspace \
     --no-default-features \
     --features system-harfbuzz,system-fribidi
   ```

4. Make the smallest change that solves the problem.
5. Add or update a test fixture when behavior changes.
6. Run formatting, linting, and tests:

   ```bash
   cargo fmt --all -- --check
   cargo clippy --workspace --all-targets -- -D warnings
   cargo test --workspace
   ```

   On systems with the Noto fixture fonts, Poppler, and Ghostscript installed, also run:

   ```bash
   scripts/validate-system-fonts.sh
   ```

   An Arabic `poppler_exact=False` result is currently expected and tracked as a known reader-side BiDi limitation. Khmer and Devanagari should report exact extraction.

## Unicode and shaping changes

Changes involving text shaping or extraction should include a fixture that demonstrates the exact logical source text.

Good fixtures target cases such as:

- multiple Unicode code points becoming one glyph;
- one logical cluster becoming several positioned glyphs;
- reordered marks;
- contextual substitutions;
- combining marks;
- ZWJ sequences;
- RTL and mixed BiDi content;
- whitespace and automatic line breaking;
- font fallback.

Please include the expected extracted text as UTF-8, not only a screenshot.

## PDF interoperability reports

When reporting a reader-specific issue, include:

- the smallest PDF that reproduces it;
- the original logical Unicode text;
- the copied/extracted result;
- the PDF reader and version if known;
- whether the visual rendering is correct;
- whether `/ToUnicode`, `/ActualText`, or Tagged PDF content is involved.

Do not report only that text "looks wrong". Rendering correctness and Unicode extraction correctness are separate dimensions.

## Architecture changes

For changes to the central text model, explain how the proposal handles:

1. logical Unicode order;
2. visual glyph order and geometry;
3. contextual shaping;
4. CID reuse;
5. `/ToUnicode` generation;
6. source-span mapping when present;
7. RTL and mixed BiDi text.

## Generated artifacts

Do not commit large generated PDFs, fonts, screenshots, or experiment output unless they are intentionally small conformance fixtures.

The default `.gitignore` excludes generated experiment artifacts.

## Commit and pull request scope

Prefer focused pull requests. A useful pull request should answer one clear question such as:

- "Preserve Khmer coeng clusters in the logical unit builder"
- "Allocate separate CIDs for contextually distinct Arabic shapes"
- "Encode supplementary-plane Unicode correctly in `/ToUnicode`"

Avoid combining unrelated refactors with behavioral changes.

## Licensing of contributions

This repository is licensed under Apache-2.0. By submitting a contribution, you agree that your contribution is provided under the same Apache-2.0 license and that you have the right to submit it.

Do not add code, fonts, PDFs, or other assets with unclear or incompatible licensing.

## Reader conformance changes

Changes that affect PDF text semantics, font encoding, BiDi placement, `/ToUnicode`, `/ActualText`, or Tagged PDF structure should run the reader matrix when the optional tools are available:

```bash
scripts/run-conformance.sh --check-baseline
```

When changing an expected interoperability result, update `conformance/baseline.json` only after attaching a reproducible generated PDF and reader/version evidence. Do not turn a failure into a baseline pass based only on visual rendering.

For manual Acrobat/Preview/browser clipboard results, save the pasted UTF-8 text and verify it with `conformance/compare_copy.py`; include the exact reader and operating-system version in the report.
