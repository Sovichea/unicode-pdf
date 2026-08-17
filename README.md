# unicode-pdf

Unicode-correct PDF generation for Rust.

`unicode-pdf` preserves logical Unicode independently from complex text shaping, so the text copied or extracted from a PDF does not have to be reconstructed from final glyph IDs. The project is motivated by long-standing copy/paste failures in Khmer and other complex scripts, and is validated across Khmer, Devanagari, Arabic, emoji/ZWJ sequences, mixed BiDi text, font fallback, wrapping, pagination, and multiple PDF readers.

> Status: early alpha. The core Unicode model, shaping pipeline, Type0/CIDFontType2 writer, tagged paragraphs, font fallback, paragraph layout, and cross-reader conformance harness are implemented. Font-format coverage and output optimization are still incomplete.

## Quick start

Add the crate to your application:

```toml
[dependencies]
unicode-pdf = "0.1.0-alpha.3"
```

The default feature set is pure Rust:

- [`harfrust`](https://crates.io/crates/harfrust) 0.13 for OpenType shaping.
- [`unicode-bidi`](https://crates.io/crates/unicode-bidi) 0.3 for the Unicode Bidirectional Algorithm.

No system HarfBuzz or FriBidi installation is required for the default build.

```rust,no_run
use std::fs;
use unicode_pdf::{Document, Font};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let font = Font::from_bytes(
        "KhmerBody",
        fs::read("NotoSansKhmer-Regular.ttf")?,
    );

    let mut document = Document::new();
    document.add_font(font);
    document.paragraph("កម្ពុជា ខ្ញុំសរសេរភាសាខ្មែរ។");

    let pdf = document.finish()?;
    fs::write("out.pdf", pdf.bytes())?;
    Ok(())
}
```

For a very small one-paragraph program you can also use `render_text`:

```rust,no_run
use std::fs;
use unicode_pdf::{render_text, Font};

let font = Font::from_bytes("Body", fs::read("font.ttf")?);
let pdf = render_text("Hello កម្ពុជា", font)?;
fs::write("out.pdf", pdf.bytes())?;
# Ok::<(), Box<dyn std::error::Error>>(())
```

## Why this crate exists

A PDF producer normally shapes Unicode into positioned glyphs before writing the page. For complex scripts that transformation may merge, split, reorder, or contextually substitute glyphs. Reconstructing text from those final glyph IDs is therefore unreliable.

`unicode-pdf` keeps the original logical text alongside the shaped visual representation:

```text
logical Unicode
      |
      +-----------------------------+
      |                             |
      v                             v
BiDi + shaping                exact semantic text
      |                             |
      v                             v
positioned glyphs             authoritative /ToUnicode
      |                             |
      +--------------+--------------+
                     |
                     v
                    PDF
```

The central internal abstraction is `LogicalPdfUnit`, which records exact logical Unicode plus the glyph components that render that unit. A complex Khmer cluster, Indic conjunct, ligature, contextual Arabic form, or emoji ZWJ sequence can therefore remain one unambiguous PDF text unit even when it renders through multiple glyph components.

## Public API strategy

Only two workspace packages are intended for users:

- `unicode-pdf`: the public Rust library.
- `unicode-pdf-cli`: a development and conformance CLI. It is not published.

The library exposes two levels:

1. `Document`, `Font`, and `render_text` for applications that want built-in shaping, fallback, line layout, pagination, and PDF generation.
2. Lower-level `core`, `shape`, `bidi`, `layout`, `font`, and `pdf` modules for typesetters that already own part of the pipeline.

`Document::finish_with` accepts caller-provided `TextShaper` and `BidiResolver` implementations, so advanced applications can choose a backend without changing the PDF model.

## Font fallback

Fonts are supplied explicitly as bytes for deterministic and server-friendly builds:

```rust,no_run
use std::fs;
use unicode_pdf::{Document, Font};

let mut document = Document::new();
document.add_font(Font::from_bytes("Latin", fs::read("NotoSans-Regular.ttf")?));
document.add_font(Font::from_bytes("Khmer", fs::read("NotoSansKhmer-Regular.ttf")?));
document.add_font(Font::from_bytes("Arabic", fs::read("NotoSansArabic-Regular.ttf")?));
document.paragraph("English កម្ពុជា العربية");
let pdf = document.finish()?;
# Ok::<(), Box<dyn std::error::Error>>(())
```

Fallback is based on each font's actual Unicode `cmap` coverage rather than its filename or script name.

## Language-aware line breaking

Line-break opportunities are layout metadata. They never modify the logical Unicode stored in the PDF.

Applications can obtain break offsets from ICU4X, a Khmer segmenter, or another language-aware boundary engine and pass UTF-8 byte offsets to the document:

```rust,no_run
# use unicode_pdf::Document;
# let mut document = Document::new();
# let text = String::from("ការអប់រំមានតួនាទីសំខាន់");
# let break_offsets = vec![12usize];
document.set_text(text);
document.set_break_opportunities(break_offsets);
```

The compiler does not insert U+200B or other synthetic word-break characters into copied PDF text.

## Shaping and BiDi backends

### Default: pure Rust

```text
harfrust + unicode-bidi
```

This is the intended configuration for normal crate consumers. HarfRust 0.13 is a safe-Rust HarfBuzz port and has an MSRV of Rust 1.85. Its project documents a small set of HarfBuzz conformance gaps, so this repository continues to test against system HarfBuzz as a reference backend.

### Optional: system HarfBuzz/FriBidi

The existing native adapters are retained for conformance testing and applications that explicitly prefer those libraries:

```toml
[dependencies]
unicode-pdf = {
    version = "0.1.0-alpha.3",
    default-features = false,
    features = ["system-harfbuzz", "system-fribidi"]
}
```

On Unix-like systems this dynamically loads the installed HarfBuzz and FriBidi shared libraries.

You can also use both sets of features and call `Document::finish_with` with a specific backend.

## Building the repository

The workspace declares Rust 1.85 as its MSRV.

On a normal internet-connected development machine:

```bash
cargo build --workspace
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
```

The first Cargo invocation downloads HarfRust, `unicode-bidi`, and their transitive crates from crates.io.

To test the native reference backends on Unix:

```bash
cargo test --workspace \
  --no-default-features \
  --features system-harfbuzz,system-fribidi
```

The sandbox used during development has no crates.io access, so its validation path uses the system HarfBuzz/FriBidi features. CI tests the normal pure-Rust default configuration on GitHub as well as the optional native configuration.

## Examples

The repository includes examples that accept fonts at runtime, so font binaries are never committed:

```bash
cargo run -p unicode-pdf --example khmer -- \
  /path/to/NotoSansKhmer-Regular.ttf \
  /tmp/khmer.pdf

cargo run -p unicode-pdf --example multilingual -- \
  /tmp/multilingual.pdf \
  /path/to/NotoSans-Regular.ttf \
  /path/to/NotoSansKhmer-Regular.ttf \
  /path/to/NotoSansArabic-Regular.ttf \
  /path/to/NotoSansDevanagari-Regular.ttf

cargo run -p unicode-pdf --example external_breaks -- \
  /path/to/NotoSansKhmer-Regular.ttf \
  fixtures/khmer-paragraph-natural.txt \
  fixtures/khmer-paragraph-natural.breaks.txt \
  /tmp/khmer-paragraph.pdf
```

## Development CLI

The CLI exposes lower-level research and validation paths:

```bash
cargo run -p unicode-pdf-cli -- inspect fixtures/khmer.txt
cargo run -p unicode-pdf-cli -- shape FONT.ttf fixtures/khmer.txt
cargo run -p unicode-pdf-cli -- synthesize-font FONT.ttf fixtures/khmer.txt out.ttf
cargo run -p unicode-pdf-cli -- emit-pdf FONT.ttf fixtures/khmer.txt out.pdf
cargo run -p unicode-pdf-cli -- emit-layout-pdf INPUT.txt out.pdf FONT.ttf [FONT.ttf ...]
```

## Unicode contract

The compiler treats input logical Unicode as authoritative:

- shaping does not rewrite semantic text;
- visual line wrapping does not insert newlines;
- pagination does not insert newlines;
- language-aware break opportunities are metadata only;
- `/ToUnicode` is generated from preserved logical text, not reverse-mapped from final glyph IDs;
- source synchronization metadata is optional and separate from PDF text semantics.

See [docs/UNICODE_CONTRACT.md](docs/UNICODE_CONTRACT.md).

## Conformance

The repository contains a cross-reader harness for Poppler, MuPDF, PDFium, and PDF.js plus geometry and browser-selection diagnostics.

```bash
scripts/run-conformance.sh
```

Reader-generated whitespace and BiDi changes remain visible as reader behavior rather than being normalized into compiler passes. In particular, the PDFium harness can identify CR/LF characters that PDFium itself synthesizes from visual line geometry.

See [conformance/README.md](conformance/README.md) and [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md).

## Current limitations

The current synthetic-font path is focused on horizontal `glyf`-based TrueType fonts. Work remains for CFF/CFF2 outlines, variable-font materialization, color fonts, vertical writing, true font subsetting, compression, advanced justification, hyphenation semantics, and full PDF/UA/PDF-A production validation.

HarfRust also documents a small number of conformance differences from HarfBuzz. The optional system-HarfBuzz backend remains part of the regression strategy for this reason.

## Repository layout

```text
unicode-pdf/
├── crates/
│   ├── unicode-pdf/       # public library crate
│   └── unicode-pdf-cli/   # development CLI, publish = false
├── conformance/           # reader/extraction/geometry harnesses
├── docs/                  # architecture, contracts, milestone reports
├── experiments/           # historical proof-of-concept work
├── fixtures/              # UTF-8 conformance inputs, no bundled fonts
├── integrations/pdfjs/    # optional PDF.js compatibility research
└── scripts/
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
