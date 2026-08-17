# Cross-reader conformance harness

This directory measures whether a generated PDF returns the original logical Unicode in independent PDF engines.

The harness currently supports:

- Poppler through `pdftotext`;
- MuPDF through the optional `PyMuPDF`/`fitz` Python binding;
- PDFium through the optional `pypdfium2` Python binding;
- PDF.js through an external PDF.js distribution and Node.js.

The same generated PDF is passed to every available reader. This is deliberate: if one engine returns exact Unicode while another changes it, the result identifies a consumer interoperability difference rather than a missing compiler mapping.

## Run

With the readers already installed:

```bash
scripts/run-conformance.sh
```

To include stock PDF.js:

```bash
scripts/run-conformance.sh --pdfjs-dist /path/to/pdfjs-dist
```

`--pdfjs-dist` can point either to the distribution root or directly to `build/pdf.mjs`. The `PDFJS_DIST` environment variable is equivalent.

Require specific readers and fail if one is missing:

```bash
scripts/run-conformance.sh \
  --require poppler,mupdf,pdfium,pdfjs \
  --pdfjs-dist /path/to/pdfjs-dist
```

Detect regressions against `baseline.json`:

```bash
scripts/run-conformance.sh --check-baseline
```

Known failures do not make the baseline check fail. If a known failure becomes exact, it is reported as an improvement. A case marked `pass` becoming non-exact is a regression.

## What counts as exact

Two comparisons are recorded:

- `raw_exact`: byte-for-byte decoded text equality with the fixture;
- `selection_exact`: equality after normalizing only reader/page transport behavior.

Selection normalization does exactly three things:

1. CRLF and CR become LF;
2. form-feed page separators are removed;
3. trailing LF characters are ignored.

It deliberately does **not** apply NFC/NFKC, strip BiDi controls, reorder combining marks, remove or insert spaces, or reconstruct reading order. Those changes are part of the behavior under test.

Results are written to `target/conformance/results.json` and `target/conformance/RESULTS.md` by default. Raw reader output is retained under `target/conformance/extracted/<reader>/<case>.txt` so failures can be inspected or fed directly to the clipboard comparator.

## UI copy/paste

Extraction APIs are not identical to a user's clipboard operation. Acrobat, Preview, Chrome/Edge PDF viewer, Firefox PDF.js viewer, and mobile readers therefore remain release-level manual tests. See [`manual/COPY_PASTE_CHECKLIST.md`](manual/COPY_PASTE_CHECKLIST.md).

## Multi-font layout fixtures

Cases with `"generator": "layout"` use the `emit-layout-pdf` path and may provide an ordered `fonts` array. Each font entry has its own environment override and Fontconfig family. This lets CI exercise real fallback instead of relying on one unusually broad font.

The current layout fixtures intentionally cover three different questions:

- `multifont-inline`: one short Latin + Khmer + Devanagari line. This is an exact-copy regression case and currently passes Poppler, MuPDF, and PDFium.
- `multifont-layout`: wrapped paragraphs with Latin, Khmer, Arabic, and Devanagari. Strict extraction remains a known failure because readers synthesize visual line breaks and apply script-specific reconstruction.
- `multifont-multipage`: the same model across five physical pages, primarily guarding pagination, page-local MCIDs, font resources, and reader stability.

Soft line breaks are not normalized away by the conformance harness. This is intentional: the Unicode preservation contract says visual wrapping should not become semantic whitespace. The known-fail baseline keeps that interoperability gap visible rather than hiding it in comparison code.

## Typsastra PDF.js browser selection

`run_geometry.py` measures reader text boxes, but Typsastra needs the actual browser text layer to behave correctly. `run_pdfjs_typsastra.py` therefore performs a separate end-to-end PDF.js compatibility test:

1. generate the mixed-font PDF and compiler `GeometryIndex`;
2. copy and patch `pdfjs-dist` with `integrations/pdfjs/apply_typsastra_patch.py`;
3. extract stock and `preserveLogicalText` TextContent;
4. render PDF.js's real `TextLayer` in a browser through Playwright;
5. create DOM `Range` selections for Khmer, Devanagari, cross-font, and full-line cases;
6. compare `Selection.toString()` and `Range.getClientRects()` against source Unicode and compiler geometry.

Run it with:

```bash
python conformance/run_pdfjs_typsastra.py \
  --pdfjs-dist /path/to/pdfjs-dist \
  --browser chromium \
  --check-baseline
```

Add `--screenshots` to retain before/after browser selection images. The harness also supports `--browser firefox` when a Playwright Firefox executable is available.
