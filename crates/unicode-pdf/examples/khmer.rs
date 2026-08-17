//! Minimal Khmer PDF example using a runtime-supplied font.

use std::{env, fs, process::ExitCode};

use unicode_pdf::{Document, Font};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args().skip(1);
    let font_path = args
        .next()
        .ok_or("usage: cargo run -p unicode-pdf --example khmer -- <font.ttf> <out.pdf>")?;
    let output_path = args
        .next()
        .ok_or("usage: cargo run -p unicode-pdf --example khmer -- <font.ttf> <out.pdf>")?;

    let font_bytes = fs::read(&font_path)?;
    let mut document = Document::new();
    document.add_font(Font::from_bytes("KhmerBody", font_bytes));
    document.paragraph("កម្ពុជា ខ្ញុំសរសេរភាសាខ្មែរ។");

    let pdf = document.finish()?;
    fs::write(output_path, pdf.bytes())?;
    Ok(())
}
