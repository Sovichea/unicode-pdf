# Cross-script cluster-CID validation

## Result

The proposed validation order was useful because Arabic exposed the first generalization problem before we moved on to simpler LTR scripts.

Final exact-extraction tests using the Typsastra logical-text PDF.js mode:

| Case | Test text | Visual model | Exact `getTextContent()` |
|---|---|---|---|
| Arabic | `العَرَبِيَّة لا مُحَمَّد` | real HarfBuzz-shaped synthetic composite CIDs, logically ordered PDF operators at visual RTL positions | PASS |
| Devanagari | `क्षेत्र में हिन्दी की परीक्षा` | real HarfBuzz-shaped synthetic composite CIDs | PASS |
| Emoji/ZWJ | `👨‍👩‍👧‍👦 👍🏽 ❤️‍🔥` | one semantic CID per HarfBuzz/emoji cluster, color visual fixture | PASS |
| Mixed LTR/RTL | `Version 2: العربية 2026 | path /docs ثم English again.` | logical semantic CIDs + BiDi visual fixture | PASS |

All final comparisons are byte/code-point exact against the source strings.

## What unmodified PDF.js 6.2.108 does

### Arabic

A logical-order CID stream is reordered by PDF.js's internal BiDi pass:

Source:

`العَرَبِيَّة لا مُحَمَّد`

Unmodified extraction:

`د َّمَحُم ال ةَّيِبَرَعلا`

Putting CIDs into visual order does not fully solve it because combining marks inside multi-codepoint cluster mappings are reordered independently:

`الَعَرِبَّية ال ُمَحَّمد`

### Devanagari

The simple semantic fixture already extracted exactly, but the production-style synthetic glyph fixture produced an inferred space:

`क्षेत्र में हिन् दी की परीक्षा`

The cause is the same multi-codepoint category bug found in the Khmer test. A `/ToUnicode` value containing an `Mn` mark can cause PDF.js to classify the whole multi-codepoint CID as a zero-width diacritic.

### Emoji / ZWJ

The tested sequences extracted exactly even in unmodified PDF.js:

`👨‍👩‍👧‍👦 👍🏽 ❤️‍🔥`

The category fix is still desirable because it makes CID width accounting correct for multi-codepoint values containing ZWJ/format characters.

### Mixed LTR/RTL

Source:

`Version 2: العربية 2026 | path /docs ثم English again.`

Unmodified PDF.js on logical-order semantic CIDs returns visual/BiDi-reordered text:

`Version 2: 2026 ةيبرعلا | path /docs مث English again.`

Even a visual-order CID stream does not reconstruct the original source order around the Arabic run and the following number.

## PDF.js changes required for the tested logical-text model

The proof-of-concept uses three changes:

1. Treat `Mn`, `Cf`, and whitespace as special only when the *entire* `/ToUnicode` string is one such character, rather than when a multi-codepoint mapping merely contains one.
2. Preserve the compiler-provided logical Unicode string in `getTextContent()` instead of applying PDF.js's BiDi reordering to it. BiDi can still be used to report `dir`.
3. Preserve explicit whitespace from the compiler rather than discarding it and reconstructing spaces from geometry.

The experimental worker diff is in `pdfjs-logical-text-mode.patch`.

These should become an explicit Typsastra mode/API option rather than global behavioral changes in a production fork.

## Important compiler generalization

A CID cannot be keyed only by its Unicode cluster string for every script.

Arabic contextual shaping demonstrates why. Shaping `ببب` gives the same logical cluster text `ب` three times, but HarfBuzz produces three different contextual glyph IDs:

- final form: GID 101
- medial form: GID 104
- initial form: GID 102

Therefore the reusable visual-CID key should be based on something like:

`(logical Unicode, shaped glyph sequence, component positions, font/style)`

not only:

`logical Unicode`

Multiple visual CIDs may legitimately share the same `/ToUnicode` value.

## Architecture supported by the experiment

A general compiler can retain two independent orders:

- **logical order:** original Unicode/source order used for semantic PDF operators and source mapping;
- **visual geometry:** HarfBuzz/BiDi-derived glyph positions used to place those logical CIDs on the page.

For Arabic, the production-style fixture emitted each cluster in logical source order but placed it at the HarfBuzz-derived visual RTL x-coordinate. The rendered line looked correct and the logical-text PDF.js mode returned the exact original source string.

For LTR complex scripts such as Devanagari, logical and cluster visual order are usually aligned, so the model is simpler.

For emoji, the semantic CID can represent the entire ZWJ sequence while the visual side can be a color glyph, vector graphic, or image.

## Remaining validation

The main remaining gap is a fully production-style mixed LTR/RTL page using real font glyphs and absolute BiDi geometry rather than the raster-backed visual fixture. The semantic extraction problem is solved by the logical-text mode, but selection/highlight geometry across multiple BiDi runs should still be tested.
