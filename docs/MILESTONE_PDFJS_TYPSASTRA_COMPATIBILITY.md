# Milestone: Typsastra PDF.js selection compatibility

Typsastra's live preview depends on PDF.js, so browser selection is a product
requirement rather than an optional reader-compatibility target. This milestone
adds a narrow PDF.js compatibility mode for PDFs emitted by `unicode-pdf`.

## Problem reproduced

The compiler knows that the Khmer run

```text
កម្ពុជា ខ្ញុំ
```

occupies PDF x≈165.664..208.910. Stock PDF.js 6.2.108 instead exposed a
`getTextContent()` item for the run ending around x≈187.49. The browser text
layer therefore compressed the selectable range into roughly half of the
visible Khmer text even though the PDF rendered correctly and Acrobat selected
it correctly.

The root causes were cumulative:

1. PDF.js's special-character regexp matched `Mn`/`Cf` anywhere inside a
   multi-codepoint `/ToUnicode` value, so a complete logical CID could be
   treated like a zero-width diacritic.
2. Stock extraction deletes explicit spaces and later reconstructs spacing from
   geometry.
3. PDF.js applies another BiDi transformation to decoded Unicode even when the
   compiler already emitted semantic CIDs in logical source order.
4. Several complex logical CIDs were combined into one browser span with one
   aggregate width. The browser then had to redistribute that width across its
   own reshaping of the Unicode string.
5. Single-character TextLayer items, including explicit spaces, were not scaled
   to their exact PDF width.

## Production-oriented compatibility mode

`integrations/pdfjs/apply_typsastra_patch.py` adds an opt-in
`preserveLogicalText` option to `pdfjs-dist` 6.2.108.

When enabled:

- explicit PDF whitespace is retained;
- PDF.js still computes `dir`, but returns the compiler's logical Unicode order;
- multi-codepoint CID classification is based on the whole mapping;
- multi-codepoint logical glyphs are isolated into individual TextContent items;
- all logical-mode TextLayer spans use the PDF-provided width;
- logical-mode clipboard copy skips PDF.js compatibility normalization so the
  selected Unicode is preserved exactly.

Normal PDF.js behavior remains the default. This matters because arbitrary PDFs
may use visual content-stream order and depend on PDF.js's normal BiDi and
whitespace reconstruction.

## Typsastra integration

Enable logical-text mode on the stream feeding the text layer:

```js
const textContentSource = page.streamTextContent({
  includeMarkedContent: true,
  disableNormalization: true,
  preserveLogicalText: true,
});

const textLayer = new pdfjsLib.TextLayer({
  textContentSource,
  container: textLayerDiv,
  viewport,
});
await textLayer.render();
```

`TextLayerBuilder.render()` can receive the same setting through
`textContentParams`.

## Real DOM selection test

The new conformance harness does not stop at `getTextContent()` rectangles. It
loads PDF.js's real `TextLayer` class into a browser, creates DOM `Range`
selections, reads `Selection.toString()`, and compares `Range.getClientRects()`
with compiler `GeometryIndex`.

Validated locally with Chromium 144 and PDF.js 6.2.108:

| Selection | Stock geometry | Patched geometry | Stock edge error | Patched edge error | Patched copy |
|---|---:|---:|---:|---:|---|
| Khmer `កម្ពុជា` | 50.11% | 99.98% | 15.32 px | 0.02 px | exact |
| Khmer `ខ្ញុំ` | 0.00% | 99.87% | 21.44 px | 0.01 px | exact |
| Khmer run | 50.42% | 100.00% | 21.44 px | 0.02 px | exact |
| Devanagari `हिन्दी` | 51.57% | 100.00% | 12.73 px | 0.02 px | exact |
| Cross-font selection | 89.15% | 99.53% | 12.73 px | 0.59 px | exact |
| Full line | not selectable as exact source | 99.99% | n/a | 0.02 px | exact |

The especially important result is `ខ្ញុំ`: stock PDF.js placed its selection
rectangle completely outside the compiler's expected rectangle, while logical
mode overlaps 99.87% with an approximately 0.01 px horizontal edge error.

## Exact TextContent produced in logical mode

Instead of one compressed Khmer item, PDF.js now exposes the PDF's actual
logical units and advances:

```text
ក       8.904 pt
ម្ពុ    8.890 pt
ជា     12.922 pt
space   3.640 pt
ខ្ញុំ    8.890 pt
space   3.640 pt
```

This matches the same semantic units used by the compiler's `/ToUnicode` and
geometry index.

## Firefox status

The original user-visible failure was observed in Firefox's PDF.js viewer. The
patch targets PDF.js's shared worker/TextLayer code rather than Chromium-specific
behavior. The sandbox could not download a Firefox binary because outbound DNS
is disabled, so this milestone's automated browser run is Chromium-only here.
The harness supports a Firefox browser mode and CI is configured to run the same
DOM selection checks in Playwright Firefox where network installation is
available.

Until that Firefox CI/manual check has run, we should describe the result as
"PDF.js TextLayer fixed and Chromium-validated", not yet as a verified Firefox
release result.

## Files

- `integrations/pdfjs/apply_typsastra_patch.py`
- `integrations/pdfjs/pdfjs-dist-6.2.108-typsastra.patch`
- `conformance/pdfjs_text_content.mjs`
- `conformance/pdfjs_textlayer_probe.py`
- `conformance/run_pdfjs_typsastra.py`
- `conformance/pdfjs_typsastra_cases.json`
- `conformance/pdfjs_typsastra_baseline.json`
