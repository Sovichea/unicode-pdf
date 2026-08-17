# Milestone: Semantic paragraph fidelity across visual layout boundaries

This milestone makes a logical paragraph a compiler-owned semantic object instead of treating visual lines as the only durable text grouping.

## Goal

A source paragraph must remain the same Unicode string after wrapping and pagination:

```text
source paragraph
      |
      +--> language-aware break opportunities
      |
      +--> visual line 1
      +--> visual line 2
      +--> ...
      +--> next physical page

logical paragraph == original source paragraph
```

Soft line breaks and page transitions are geometry. They are not Unicode characters unless the source explicitly contained such characters.

## Implementation

`unicode-pdf-layout` now retains:

```rust
pub struct LogicalParagraph {
    pub paragraph_index: u32,
    pub source_range: SourceRange,
    pub unicode: String,
    pub terminated_by_newline: bool,
}
```

`LayoutDocument::logical_text()` reconstructs the original document from these semantic paragraphs. It restores hard source newlines only. It does not include soft wraps or page transitions.

The layout engine accepts external UTF-8 byte-offset break opportunities. The source string is never edited with spaces, zero-width spaces, or line-break characters. The included Khmer paragraph fixtures use byte offsets produced by the standalone Khmer segmenter.

The Tagged PDF hierarchy is now:

```text
StructTreeRoot
  Document
    P
      Span -> MCID
      Span -> MCID
      ...
```

The `/P` structure element can carry exact compiler-owned paragraph Unicode in `/ActualText`. The layout CLI uses this structure-level policy by default.

## Paragraph `/ActualText` policies

Four policies were tested on the same seven-line natural Khmer paragraph:

- `off`
- `structure`
- `page-fragment`
- `structure-and-page-fragment`

The `structure` policy puts replacement text on the `/P` structure element without modifying page content. It is the production default because it preserves reader behavior while providing exact semantic text in the tagged structure.

`page-fragment` wraps all visual content belonging to one paragraph fragment on a physical page with one marked-content `/ActualText` string. This is useful as an interoperability experiment, but reader behavior is inconsistent:

| Reader | `structure` | `page-fragment` observation |
|---|---|---|
| Poppler 25.06.0 | reconstructs visual newlines | recovers the one-page paragraph continuously |
| MuPDF 1.26.12 | reconstructs visual newlines | ignores the fragment replacement and still reconstructs lines |
| PDFium 149.0.7825.0 | reconstructs visual newlines | text extraction becomes empty in the tested binding |
| PDF.js 6.2.108 | exact continuous Khmer for the natural fixture | remains exact |

Because page-fragment replacement regresses PDFium, it is opt-in and not used by default.

The policy can be selected for development experiments with:

```bash
UNICODE_PDF_PARAGRAPH_TEXT_POLICY=off
UNICODE_PDF_PARAGRAPH_TEXT_POLICY=structure
UNICODE_PDF_PARAGRAPH_TEXT_POLICY=page-fragment
UNICODE_PDF_PARAGRAPH_TEXT_POLICY=structure-and-page-fragment
```

## Natural Khmer fixtures

Two compiler-side fixtures were added:

```text
fixtures/khmer-paragraph-natural.txt
fixtures/khmer-paragraph-natural.breaks.txt

fixtures/khmer-paragraph-multipage.txt
fixtures/khmer-paragraph-multipage.breaks.txt
```

The source contains natural continuous Khmer writing. The break files contain UTF-8 byte offsets only and are not inserted into the source text.

The one-page fixture wraps to seven visual lines. The multi-page fixture repeats the same natural source into one continuous semantic paragraph and crosses a physical page boundary.

## PDF-level semantic conformance

`conformance/check_paragraph_semantics.py` verifies the generated PDF itself rather than depending on reader text heuristics. It checks that:

1. the structure tree contains a `/Document` element;
2. the expected `/P` structure elements exist;
3. each paragraph `/ActualText` decodes from UTF-16BE to exactly the pre-layout source paragraph.

Both one-page and multi-page Khmer fixtures pass this check.

## Reader extraction results

With the production `structure` policy:

| Reader | Natural 7-line paragraph | Multi-page paragraph |
|---|---:|---:|
| Poppler 25.06.0 | known fail: inserts visual line newlines | known fail: inserts visual line newlines |
| MuPDF 1.26.12 | known fail: inserts visual line newlines | known fail: inserts visual line newlines |
| PDFium 149.0.7825.0 | known fail: inserts visual line newlines | known fail: inserts visual line newlines |
| PDF.js 6.2.108 | **exact** | **exact after transport-only page-separator removal** |

The PDF.js multi-page result exposed a bug in the conformance harness itself. The adapter had joined physical pages with `\n`, incorrectly manufacturing a semantic line break. All page-oriented adapters now use form-feed as a transport separator. The comparator already removes form-feed without touching semantic Unicode.

The remaining Poppler/MuPDF/PDFium newlines are reader-side geometric reconstruction. The compiler retains exact paragraph semantics regardless.

## Acceptance criteria

This milestone is complete when:

- soft line breaking does not modify source Unicode;
- external segmentation offsets do not become PDF text characters;
- one logical paragraph can span many visual lines and pages;
- `LayoutDocument::logical_text()` equals the original source exactly;
- Tagged PDF uses `Document -> P -> Span -> MCID`;
- `/P /ActualText` equals the original paragraph exactly;
- the paragraph semantic checker passes for both one-page and multi-page fixtures;
- cross-reader differences remain visible rather than normalized away.

## Next compiler-side milestone

The next milestone should focus on **discretionary layout transformations**, especially automatic hyphenation and other visual-only insertions. The compiler needs an explicit representation for text that appears visually but must not become copied Unicode, and for source characters that may be suppressed visually but must remain semantic text.
