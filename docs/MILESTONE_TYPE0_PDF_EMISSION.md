# Milestone: Rust Type0/CIDFontType2 PDF emission

## Objective

Replace the Python PDF-generation proof of concept with an end-to-end Rust path that connects preserved logical Unicode, HarfBuzz visual geometry, synthetic TrueType glyphs, CID allocation, and `/ToUnicode` into a real PDF.

## Implemented pipeline

```text
UTF-8 source
    |
    v
system HarfBuzz adapter
    |
    v
LogicalPdfUnit[] in source order
    |
    +--> CID allocator
    |       |
    |       +--> CID -> exact logical Unicode
    |
    +--> TrueType composite synthesizer
            |
            +--> CID -> appended synthetic GID
    |
    v
Type0 / CIDFontType2 writer
    |
    +--> embedded FontFile2
    +--> CIDToGIDMap
    +--> /W widths
    +--> /ToUnicode
    +--> logical-order Tj operators
    +--> HarfBuzz visual X positions via absolute Tm
    |
    v
PDF 1.7
```

## Important ordering invariant

The content stream does not use drawing order as the semantic text order. Units are serialized in source-logical order. Each unit receives an absolute visual X coordinate derived from the shaping run.

This is what allows an RTL run to render right-to-left while retaining a logical CID sequence in the PDF content stream.

## Validation performed

The milestone was exercised with system Noto Sans fonts for:

- Khmer
- Arabic
- Devanagari

The generated PDFs were checked with Ghostscript rendering, Poppler tools, and PDF.js 6.2.108.

### Current extraction results

| Script | Visual render | Poppler `pdftotext` | Stock PDF.js 6.2.108 | PDF.js logical-text research mode |
|---|---|---|---|---|
| Khmer | correct | exact | extra inferred spaces | exact |
| Devanagari | correct | exact | extra inferred space in a multi-codepoint unit | exact |
| Arabic | correct | reader reorders marks/BiDi | reader BiDi/spacing changes | exact |

The PDF.js logical-text mode is the existing research patch that preserves compiler-provided logical order, keeps explicit whitespace, and fixes multi-codepoint category classification. It remains a diagnostic patch, not a requirement the project wants to impose on ordinary readers.

## Arabic finding

The Type0 implementation confirms that correct `/ToUnicode` alone cannot force every reader to return byte-for-byte Arabic source text. Poppler still applies its own BiDi and combining-mark transformations after reading the correct CID mappings.

Experiments with coarser word-level CIDs did not solve this because the reader applies BiDi processing to the Unicode destination string itself.

This means the next interoperability work should focus on PDF semantic structure and reader behavior rather than further CID clustering tricks.

## Next milestone status

BiDi segmentation, Tagged PDF structure, MCIDs, `/Lang`, directional writing metadata, and scoped `/ActualText` were implemented in the following milestone. See [MILESTONE_TAGGED_BIDI_SEMANTICS.md](MILESTONE_TAGGED_BIDI_SEMANTICS.md).

The remaining challenge is cross-reader RTL copy-order interoperability rather than missing semantic structure in the producer.
