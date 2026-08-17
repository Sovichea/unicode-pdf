# Typsastra PDF.js compatibility mode

`unicode-pdf` produces PDFs whose complex shaping units have authoritative
multi-codepoint `/ToUnicode` mappings and compiler-known geometry. Adobe Acrobat
can select those units directly from the PDF model. Stock PDF.js 6.2.108,
however, reconstructs its browser text layer using assumptions that are not
compatible with this representation.

This integration adds an **opt-in** `preserveLogicalText` mode to
`pdfjs-dist` 6.2.108. It is intended for PDFs produced by a compiler that already
emits semantic CIDs in original logical Unicode order, such as `unicode-pdf`.
Do not enable it blindly for arbitrary third-party PDFs because many PDFs rely
on PDF.js's normal BiDi/whitespace reconstruction behavior.

## Apply the patch

```bash
python integrations/pdfjs/apply_typsastra_patch.py \
  --input node_modules/pdfjs-dist \
  --output vendor/pdfjs-dist-typsastra
```

The script refuses versions other than 6.2.108. The equivalent reviewable
unified diff is [`pdfjs-dist-6.2.108-typsastra.patch`](pdfjs-dist-6.2.108-typsastra.patch).

The npm `pdfjs-dist` package does not include the complete generic viewer
application (`web/viewer.mjs`). In that package layout, the script patches the
core/worker/TextLayer code and skips the optional viewer clipboard hook. A full
PDF.js generic distribution that contains `web/viewer.mjs` receives the
clipboard hook as well. The conformance TextLayer test only requires the npm
package layout.

## Enable logical-text mode

When Typsastra builds the text layer directly:

```js
const textContent = page.streamTextContent({
  includeMarkedContent: true,
  disableNormalization: true,
  preserveLogicalText: true,
});

const textLayer = new pdfjsLib.TextLayer({
  textContentSource: textContent,
  container: textLayerDiv,
  viewport,
});
await textLayer.render();
```

When using PDF.js `TextLayerBuilder`, pass the same option through
`textContentParams`:

```js
await textLayerBuilder.render({
  viewport,
  textContentParams: {
    includeMarkedContent: true,
    disableNormalization: true,
    preserveLogicalText: true,
  },
});
```

Programmatic extraction uses the same option:

```js
const content = await page.getTextContent({
  disableNormalization: true,
  preserveLogicalText: true,
});
```

## What the patch changes

The patch is deliberately small:

1. It anchors PDF.js's `Mn`/`Cf` special-character regexp to the whole mapped
   string. A multi-codepoint logical CID is no longer treated as a zero-width
   diacritic merely because one scalar inside it is a combining mark.
2. Logical-text mode keeps explicit PDF whitespace instead of deleting spaces
   and later inferring them from geometry.
3. PDF.js still computes the item's direction, but logical-text mode returns the
   compiler-provided Unicode string instead of running a second BiDi reorder on
   it.
4. Multi-codepoint PDF glyphs are isolated into their own TextContent items.
   This gives the DOM text layer an exact PDF advance per shaping unit rather
   than asking the browser to distribute one aggregate width over several
   complex clusters.
5. TextLayer scales every logical-mode item to its PDF-provided width, including
   explicit spaces and single-codepoint items.
6. The TextLayer is marked as logical-text content, and PDF.js viewer copy handling
   skips its compatibility normalization for that layer so clipboard text remains
   the compiler-provided Unicode sequence.

Logical mode also bypasses PDF.js text normalization in the worker. No PDF rendering code is changed. The visible canvas remains the normal PDF.js
rendering path.

## Why isolation matters

For the current mixed-script fixture, stock PDF.js creates one Khmer item:

```text
"កម្ពុជា ខ្ញុំ"  width ≈ 21.83 pt
```

while the compiler geometry for that text occupies about 43.25 pt. Browser
selection is therefore compressed into the first half of the visible word.

Logical-text mode instead exposes the actual PDF units:

```text
ក       8.904 pt
ម្ពុ    8.890 pt
ជា     12.922 pt
space   3.640 pt
ខ្ញុំ    8.890 pt
```

The browser selection layer can then use the same advances that rendered the
PDF.

## Conformance test

With a stock PDF.js distribution and a browser supported by Playwright:

```bash
python conformance/run_pdfjs_typsastra.py \
  --pdfjs-dist /path/to/pdfjs-dist \
  --browser chromium \
  --check-baseline
```

The harness generates a PDF, records compiler `GeometryIndex`, renders both
stock and patched PDF.js TextLayers in a real browser, creates DOM `Range`
selections, checks `Selection.toString()`, and compares the browser selection
rectangles to compiler geometry.

Use `--screenshots` to retain visual before/after selection evidence.
