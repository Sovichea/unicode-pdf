# Unicode Preservation Contract

## Purpose

A visually correct PDF is not sufficient. The logical text copied or extracted from the PDF must also be correct.

## Default contract

Given logical input text `S`, extraction should produce `T` such that:

```text
T == S
```

unless the API explicitly requests and documents a semantic transformation.

The comparison is against the caller-provided Unicode sequence. The engine must not silently normalize NFC/NFD/NFKC unless requested.

## Required behavior

### Shaping

Shaping must not alter logical extraction text.

### Ligatures

A visual ligature must copy as the logical source sequence that produced it.

### Complex clusters

If several glyphs render one logical cluster, extraction must contain the logical cluster exactly once.

### Contextual forms

Different glyph shapes for the same logical character must still extract to the same logical character.

### BiDi

Visual RTL/LTR ordering must not replace logical source order in the extracted Unicode.

### Combining marks

Combining marks must remain associated with their original logical sequence and must not be duplicated, removed, or independently reordered by the PDF producer.

### ZWJ and format characters

Semantic ZWJ and other format characters must be preserved when they are part of the original logical text.

### Whitespace

Semantic whitespace should be explicit. Viewers should not be required to infer ordinary spaces from geometry when the source contains a space.

### Visual line wrapping

A line break introduced only for layout must not necessarily become a semantic newline. Paragraph and line semantics must be decided by the producer.

### Automatic hyphenation

A hyphen inserted only to render a line break should not become copied logical text unless the source itself contains that hyphen or the caller requests that behavior.

### Font fallback

Changing fonts must not change logical extraction text.

## Conformance testing

Each fixture should contain:

- UTF-8 logical input;
- generated PDF;
- expected UTF-8 extraction;
- visual verification where shaping is relevant.

The project should test multiple independent extractors and real copy/paste paths.
