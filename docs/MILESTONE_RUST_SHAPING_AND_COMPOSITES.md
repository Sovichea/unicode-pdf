# Milestone: Rust shaping and TrueType composite synthesis

Date: 2026-08-16

## Scope

This milestone moves the two critical pieces of the Python proof of concept into the Rust workspace:

1. a backend-neutral shaping interface plus a runtime-loaded system HarfBuzz adapter;
2. real `glyf`-based TrueType composite-glyph synthesis for reusable logical PDF CIDs.

It does **not** yet emit a complete PDF. The next milestone is Type0/CIDFontType2 PDF emission using the synthesized font, `/CIDToGIDMap`, visual coordinates, widths, and authoritative `/ToUnicode` mappings.

## Shaping validation

The Rust HarfBuzz adapter was exercised with Noto Sans fonts installed in the development sandbox.

### Khmer

Input:

```text
កម្ពុជា ខ្ញុំសរសេរភាសាខ្មែរ
```

Result:

- direction: LTR;
- 13 logical units;
- exact logical Unicode round-trip: pass;
- multi-glyph logical units include `ម្ពុ`, `ខ្ញុំ`, and `ខ្មែ`.

### Arabic

Input:

```text
العَرَبِيَّة لا مُحَمَّد
```

Result:

- direction: RTL;
- 14 logical units reconstructed in source order;
- exact logical Unicode round-trip: pass;
- contextual forms and combining marks remain associated with the original source clusters.

### Devanagari

Input:

```text
क्षेत्र में हिन्दी की परीक्षा
```

Result:

- direction: LTR;
- 14 logical units;
- exact logical Unicode round-trip: pass;
- conjuncts and reordered matras remain represented by their original logical Unicode spans.

## TrueType synthesis validation

For each script above the Rust synthesizer:

- parsed the original sfnt tables;
- appended one composite glyph per unique CID entry;
- rewrote `glyf`, long-format `loca`, full `hmtx`, `hhea`, `maxp`, and `head`;
- converted `post` to format 3 when available;
- removed shaping tables from the synthesized output because shaping has already happened;
- rebuilt table checksums and `checkSumAdjustment`.

The resulting fonts were reopened with FontTools. Glyph counts, `loca`, and `hmtx` counts were consistent, and every appended non-empty synthetic glyph could be recursively decomposed into its component outlines.

## Safety boundary

Workspace crates use `unsafe_code = "deny"` by default. The system HarfBuzz adapter explicitly permits unsafe code because it owns the dynamic-library/FFI boundary. No other production crate needs unsafe code for this milestone.

## Known limits

- Unix-like runtime loader only.
- System HarfBuzz is required by the current adapter.
- `glyf`-based TrueType fonts only.
- No TTC/OTC collection support.
- Horizontal metrics only.
- No base-font subsetting yet.
- No CFF/CFF2 synthesis.
- No variable-font instance materialization.
- Complete PDF emission is the next milestone.
