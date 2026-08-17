# unicode-pdf

`unicode-pdf` is a Rust library for Unicode-correct PDF text generation. It preserves logical Unicode independently from shaped glyph geometry and uses authoritative `/ToUnicode` mappings for complex scripts.

The default backend is pure Rust using HarfRust and `unicode-bidi`.

```rust,no_run
use std::fs;
use unicode_pdf::{Document, Font};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut document = Document::new();
    document.add_font(Font::from_bytes(
        "KhmerBody",
        fs::read("NotoSansKhmer-Regular.ttf")?,
    ));
    document.paragraph("កម្ពុជា ខ្ញុំសរសេរភាសាខ្មែរ។");

    let pdf = document.finish()?;
    fs::write("out.pdf", pdf.bytes())?;
    Ok(())
}
```

See the repository README for architecture, conformance results, optional system HarfBuzz/FriBidi backends, examples, and current limitations.
