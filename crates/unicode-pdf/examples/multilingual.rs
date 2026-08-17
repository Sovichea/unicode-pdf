//! Multilingual fallback PDF example using runtime-supplied fonts.

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
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() < 3 {
        return Err("usage: cargo run -p unicode-pdf --example multilingual -- <out.pdf> <font.ttf> <font.ttf> [...]".into());
    }

    let output_path = &args[0];
    let mut document = Document::new();
    for (index, font_path) in args[1..].iter().enumerate() {
        document.add_font(Font::from_bytes(
            format!("Fallback{index}"),
            fs::read(font_path)?,
        ));
    }

    document.paragraph("English កម្ពុជា हिन्दी العربية 2026");
    let pdf = document.finish()?;
    fs::write(output_path, pdf.bytes())?;
    Ok(())
}
