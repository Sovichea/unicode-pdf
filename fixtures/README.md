# Conformance fixtures

Each `.txt` file contains logical Unicode that should survive PDF generation and extraction exactly.

These are seed fixtures, not a complete script conformance corpus.

Future fixtures should be small and isolate one shaping or BiDi behavior where possible.

## Natural Khmer paragraph fixtures

`khmer-paragraph-natural.txt` is continuous natural Khmer text with no layout-inserted spaces, zero-width spaces, or newlines. `khmer-paragraph-natural.breaks.txt` contains UTF-8 byte offsets produced by an external standalone Khmer segmenter and used only as legal visual line-break opportunities.

`khmer-paragraph-multipage.txt` repeats that continuous source into one long logical paragraph. Its companion break file shifts the same segmenter opportunities across the repeated text so pagination can be tested without changing semantic Unicode.

The segmenter implementation itself is not vendored into this repository; only the resulting break-offset fixture data is included.
