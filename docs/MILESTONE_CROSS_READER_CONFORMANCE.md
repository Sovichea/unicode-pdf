# Milestone: cross-reader Unicode conformance

This milestone turns reader interoperability into a repeatable test target instead of an informal manual observation.

## Implemented

### One PDF, multiple independent consumers

`conformance/run.py` generates the current Khmer, Devanagari, Arabic, and mixed-BiDi PDFs from the Rust pipeline, then passes the exact same files to independent extraction engines:

- Poppler via `pdftotext`;
- MuPDF via the optional PyMuPDF binding;
- PDFium via the optional `pypdfium2` binding;
- stock PDF.js through an external PDF.js distribution and Node.js.

The harness records exact reader versions in the result file.

### Strict comparison model

The harness records both raw equality and a selection-equivalent comparison. Selection normalization is deliberately narrow:

1. CRLF and CR become LF;
2. form-feed page separators are removed;
3. terminal LF characters are ignored.

It does not apply NFC/NFKC, delete BiDi controls, move combining marks, insert/remove spaces, or reconstruct logical order. A pass therefore means the substantive Unicode scalar sequence survived the reader.

Every failure records similarity, scalar counts, the first mismatching code-point location, Unicode names around the mismatch, and the raw extracted text in JSON.

### Regression baseline

`conformance/baseline.json` distinguishes:

- `pass`: currently required behavior; losing it is a regression;
- `known-fail`: an interoperability issue that should remain visible but does not block CI;
- an unexpected pass over `known-fail`: reported as an improvement.

This lets CI protect working Khmer/Devanagari paths without hiding known Arabic and mixed-BiDi issues.

### Clipboard comparison

`conformance/compare_copy.py` applies the same scalar-level comparison to UTF-8 text manually pasted from Acrobat, Preview, Chrome/Edge, Firefox, or mobile readers.

`conformance/manual/COPY_PASTE_CHECKLIST.md` defines the release matrix for UI-level copy/paste behavior, which is intentionally separate from extraction APIs.

## Current automated matrix

Validated in the milestone sandbox:

| Reader | Version | Khmer | Devanagari | Arabic | Mixed BiDi |
|---|---|---|---|---|---|
| Poppler | 25.06.0 | exact | exact | fail | fail |
| MuPDF | 1.26.12 via PyMuPDF 1.26.7 | exact | exact | **exact** | fail |
| PDFium | 149.0.7825.0 via pypdfium2 5.8.0 | exact | exact | fail | fail |
| PDF.js | 6.2.108 | fail | fail | fail | fail |

The MuPDF Arabic result is significant. The same generated Arabic PDF that Poppler and PDFium reorder is returned byte-for-byte by MuPDF. This demonstrates that the PDF contains sufficient logical Unicode for a mainstream engine to reconstruct the Arabic fixture correctly and narrows the remaining issue to reader interpretation/interoperability.

Stock PDF.js failures continue to match the behaviors already isolated by the diagnostic logical-text patch: inferred spacing around multi-codepoint CIDs and reader-side BiDi processing.

## CI

The repository CI now contains a pinned conformance job that installs system HarfBuzz/FriBidi/fonts/Poppler and pinned PyMuPDF, pypdfium2, and PDF.js versions, then runs:

```bash
scripts/run-conformance.sh \
  --require poppler,mupdf,pdfium,pdfjs \
  --pdfjs-dist target/conformance-node/node_modules/pdfjs-dist \
  --check-baseline
```

The JSON and Markdown reports are uploaded as CI artifacts.

## What this milestone does not claim

PDFium API extraction is not identical to Chrome/Edge clipboard behavior. PDF.js `getTextContent()` is not identical to Firefox viewer selection/copy. Acrobat and Preview do not expose equivalent stable headless APIs in this harness.

For that reason, automated extraction conformance and release-level UI copy/paste conformance remain two related but distinct matrices.

## Next milestone

The highest-value next engineering milestone is **multi-font fallback and real paragraph layout**, while continuing the conformance matrix. Mixed-script paragraphs currently require one font that covers every script, and the single-page development layout is not yet a production paragraph formatter.
