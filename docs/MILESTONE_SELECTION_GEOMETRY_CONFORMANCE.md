# Milestone: Selection and Highlight Geometry Conformance

This milestone separates **text correctness** from **selection geometry correctness**.
A reader may extract the intended Unicode while exposing bad hit boxes, fragmented highlights,
or selection regions on the wrong line/page. `unicode-pdf` now records deterministic geometry
for each logical source unit and tests reader text boxes against that geometry.

## Compiler geometry model

`unicode-pdf-layout` now exposes:

- `PdfRect`
- `UnitGeometry`
- `LineGeometry`
- `GeometryIndex::from_layout`
- `GeometryIndex::selection_rects(source_range)`

Every source-mapped logical unit carries its page, UTF-8 source range, exact Unicode, font ID,
direction, and PDF-space rectangle. Selection rectangles are coalesced per visual line and remain
separate across page boundaries.

The CLI command

```bash
unicode-pdf-cli dump-layout-geometry input.txt expected.json font1.ttf font2.ttf ...
```

writes this expected geometry as JSON for external reader tests.

## Reader probes

`conformance/run_geometry.py` generates the same fixtures and compares expected unit rectangles
against text boxes exposed by:

- Poppler (`pdftotext -bbox`)
- MuPDF / PyMuPDF character boxes
- PDFium / pypdfium2 character boxes
- stock PDF.js `getTextContent()` item geometry

The metric is spatial coverage: a non-whitespace logical unit is covered when at least one reader
text box on the same page overlaps at least 10% of its expected area. This deliberately does not
repair or normalize reader text.

## Current automated baseline

| Fixture | Poppler | MuPDF | PDFium | PDF.js |
|---|---:|---:|---:|---:|
| multifont-inline | 100.00% | 100.00% | 96.15% | 88.46% |
| multifont-layout | 100.00% | 100.00% | 98.36% | 98.69% |
| multifont-multipage | 100.00% | 100.00% | 98.09% | 98.72% |

The lower short-fixture PDF.js score corresponds to missing geometry for the final Devanagari
cluster(s), consistent with its multi-codepoint CID behavior. On longer documents, its broader text
items spatially cover most logical units even though its extracted Unicode can still fail strict
conformance.

## What this proves

Text extraction and geometry are now tracked independently:

1. `/ToUnicode` and logical source order answer what text a selection means.
2. PDF text placement answers where selection/highlight rectangles belong.
3. Reader conformance determines whether a viewer preserves both layers.

This milestone does not claim browser UI drag-selection is identical to the programmatic text-box
APIs. Manual clipboard/selection testing remains part of release qualification.
