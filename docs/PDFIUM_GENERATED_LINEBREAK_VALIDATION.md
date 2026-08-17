# PDFium generated line-break validation

## Purpose

The compiler preserves the exact logical paragraph Unicode and does not insert
soft-wrap newline characters. Chromium-family PDF viewers use PDFium, which may
reconstruct visual lines as CR/LF characters during text extraction.

This validation classifies that behavior without changing compiler output and
without normalizing it away from the strict conformance result.

## Method

For every PDFium fixture the conformance harness inspects the text page one
character at a time using PDFium's raw text API:

- `FPDFText_CountChars()`
- `FPDFText_GetUnicode()`
- `FPDFText_IsGenerated()`

The normal PDFium extraction remains untouched and is compared strictly against
the source. Separately, the diagnostic removes only CR and LF characters for
which `FPDFText_IsGenerated()` returns true and asks whether the remaining text
matches the exact source.

A case is classified as `reader-generated-line-breaks-only` only when:

1. strict extraction fails;
2. at least one generated CR/LF is present;
3. PDFium reports no other generated characters for the case; and
4. removing only those generated CR/LF characters produces the exact source
   Unicode.

This filtered form is diagnostic evidence only. It is never used to turn a
strict extraction failure into a pass.

## Current Khmer paragraph result

With pypdfium2 5.8.0 / PDFium 149.0.7825.0:

| Fixture | Generated CR/LF chars | Generated CRLF pairs | Other generated chars | Exact after removing only generated CR/LF | Classification |
|---|---:|---:|---:|---|---|
| `khmer-paragraph-natural` | 12 | 6 | 0 | yes | `reader-generated-line-breaks-only` |
| `khmer-paragraph-multipage` | 72 | 36 | 0 | yes | `reader-generated-line-breaks-only` |

For the one-page natural paragraph, the first generated pair appears at PDFium
character indices 90 and 91 as U+000D and U+000A. Every visual wrap in the
fixture is represented by another generated CR/LF pair.

## CI baseline

`conformance/baseline.json` now contains PDFium diagnostic expectations for both
Khmer paragraph fixtures. A baseline check fails if a future result no longer
matches the expected diagnosis. This keeps reader reconstruction behavior
explicit while preserving the project's compiler invariant:

> Visual line wrapping must not modify the PDF's logical source Unicode.

If a future PDFium version stops synthesizing these CR/LF pairs, the strict
extraction result should improve and the diagnostic baseline can be updated
intentionally.
