# Experiments

This directory preserves the proof-of-concept path used to validate the logical-CID architecture before it is implemented fully in Rust.

## `synthetic_cluster_pdf.py`

The script:

1. shapes the complete input string with the system HarfBuzz library;
2. groups shaped glyphs by UTF-8 source cluster;
3. reconstructs logical source spans independently of visual glyph order;
4. creates one synthetic TrueType composite glyph per logical cluster occurrence;
5. writes a Type0/CIDFontType2 PDF;
6. maps every CID directly to its original logical Unicode with `/ToUnicode`;
7. emits PDF text operators in logical order while placing glyphs at their HarfBuzz visual coordinates.

This is research code. It is intentionally direct and is not the production Rust font writer.

### Requirements

- Python 3.10+
- `fonttools`
- a system HarfBuzz shared library
- a TrueType font supported by FontTools

Example:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install fonttools

python experiments/synthetic_cluster_pdf.py \
  --font /usr/share/fonts/truetype/noto/NotoSansKhmer-Regular.ttf \
  --text "កម្ពុជា ខ្ញុំសរសេរភាសាខ្មែរ" \
  --out experiments/out/khmer.pdf
```

## PDF.js patch

`pdfjs-logical-text-mode.patch` records three PDF.js behaviors discovered during cross-script validation:

1. a multi-codepoint `/ToUnicode` value containing `Mn` or `Cf` can be classified as if the whole value were a zero-width special character;
2. PDF.js applies a BiDi transformation to extracted text, which conflicts with a producer that intentionally emits semantic CIDs in original logical order;
3. PDF.js can remove explicit whitespace and reconstruct spaces geometrically.

The generated PDF should ultimately work correctly in third-party readers without a custom patch. The patch is retained as an interoperability investigation artifact.
