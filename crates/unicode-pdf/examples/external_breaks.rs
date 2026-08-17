//! External line-break opportunity example using UTF-8 byte offsets.

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
    if args.len() != 4 {
        return Err("usage: cargo run -p unicode-pdf --example external_breaks -- <font.ttf> <text.txt> <breaks.txt> <out.pdf>".into());
    }

    let text = fs::read_to_string(&args[1])?;
    let break_offsets = fs::read_to_string(&args[2])?
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(str::trim)
        .map(str::parse::<usize>)
        .collect::<Result<Vec<_>, _>>()?;

    let mut document = Document::new();
    document.add_font(Font::from_bytes("Body", fs::read(&args[0])?));
    document.set_text(text);
    document.set_break_opportunities(break_offsets);

    let pdf = document.finish()?;
    fs::write(&args[3], pdf.bytes())?;
    Ok(())
}
