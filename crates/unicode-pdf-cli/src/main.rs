//! Development command-line utilities for inspecting and validating Unicode PDF inputs.

use std::env;
use std::fmt::Write as _;
use std::fs;
use std::process::ExitCode;

use unicode_pdf_bidi::BidiResolver;
use unicode_pdf_bidi_fribidi::FriBidiResolver;
use unicode_pdf_core::{FontId, TextDirection};
use unicode_pdf_font::{synthesize_truetype_composites, CidAllocator};
use unicode_pdf_layout::{
    layout_document, layout_document_with_break_opportunities, FontSet, GeometryIndex, LayoutFont,
    LayoutOptions,
};
use unicode_pdf_shape::{ShapeOptions, TextShaper};
use unicode_pdf_shape_harfbuzz::HarfBuzzShaper;
use unicode_pdf_write::{
    build_type0_document_pdf, build_type0_single_page_pdf, plan_text_run, ActualTextPolicy,
    DocumentParagraphText, DocumentPlacedTextRun, EmbeddedType0Font, ParagraphTextPolicy,
    PlacedTextRun, TextPlan, Type0DocumentOptions, Type0PdfOptions,
};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::FAILURE
        }
    }
}

#[allow(clippy::too_many_lines)]
fn run() -> Result<(), String> {
    let mut args = env::args().skip(1);
    let command = args.next().unwrap_or_else(|| "help".to_owned());

    match command.as_str() {
        "inspect" => {
            let path = required_arg(&mut args, "usage: unicode-pdf-cli inspect <utf8-file>")?;
            ensure_no_more_args(&mut args, "usage: unicode-pdf-cli inspect <utf8-file>")?;
            inspect(&path)
        }
        "shape" => {
            let font = required_arg(
                &mut args,
                "usage: unicode-pdf-cli shape <font.ttf> <utf8-file>",
            )?;
            let text = required_arg(
                &mut args,
                "usage: unicode-pdf-cli shape <font.ttf> <utf8-file>",
            )?;
            ensure_no_more_args(
                &mut args,
                "usage: unicode-pdf-cli shape <font.ttf> <utf8-file>",
            )?;
            shape(&font, &text)
        }
        "synthesize-font" => {
            let font = required_arg(
                &mut args,
                "usage: unicode-pdf-cli synthesize-font <font.ttf> <utf8-file> <out.ttf>",
            )?;
            let text = required_arg(
                &mut args,
                "usage: unicode-pdf-cli synthesize-font <font.ttf> <utf8-file> <out.ttf>",
            )?;
            let output = required_arg(
                &mut args,
                "usage: unicode-pdf-cli synthesize-font <font.ttf> <utf8-file> <out.ttf>",
            )?;
            ensure_no_more_args(
                &mut args,
                "usage: unicode-pdf-cli synthesize-font <font.ttf> <utf8-file> <out.ttf>",
            )?;
            synthesize_font(&font, &text, &output)
        }
        "emit-pdf" => {
            let font = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-pdf <font.ttf> <utf8-file> <out.pdf>",
            )?;
            let text = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-pdf <font.ttf> <utf8-file> <out.pdf>",
            )?;
            let output = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-pdf <font.ttf> <utf8-file> <out.pdf>",
            )?;
            ensure_no_more_args(
                &mut args,
                "usage: unicode-pdf-cli emit-pdf <font.ttf> <utf8-file> <out.pdf>",
            )?;
            emit_pdf(&font, &text, &output)
        }
        "dump-layout-geometry" => {
            let text = required_arg(
                &mut args,
                "usage: unicode-pdf-cli dump-layout-geometry <utf8-file> <out.json> <font.ttf> [font.ttf ...]",
            )?;
            let output = required_arg(
                &mut args,
                "usage: unicode-pdf-cli dump-layout-geometry <utf8-file> <out.json> <font.ttf> [font.ttf ...]",
            )?;
            let fonts: Vec<String> = args.collect();
            if fonts.is_empty() {
                return Err(
                    "usage: unicode-pdf-cli dump-layout-geometry <utf8-file> <out.json> <font.ttf> [font.ttf ...]"
                        .to_owned(),
                );
            }
            dump_layout_geometry(&fonts, &text, &output)
        }
        "emit-layout-pdf" => {
            let text = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-layout-pdf <utf8-file> <out.pdf> <font.ttf> [font.ttf ...]",
            )?;
            let output = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-layout-pdf <utf8-file> <out.pdf> <font.ttf> [font.ttf ...]",
            )?;
            let fonts: Vec<String> = args.collect();
            if fonts.is_empty() {
                return Err(
                    "usage: unicode-pdf-cli emit-layout-pdf <utf8-file> <out.pdf> <font.ttf> [font.ttf ...]"
                        .to_owned(),
                );
            }
            emit_layout_pdf(&fonts, &text, &output)
        }
        "emit-layout-pdf-breaks" => {
            let text = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-layout-pdf-breaks <utf8-file> <breaks.txt> <out.pdf> <font.ttf> [font.ttf ...]",
            )?;
            let breaks = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-layout-pdf-breaks <utf8-file> <breaks.txt> <out.pdf> <font.ttf> [font.ttf ...]",
            )?;
            let output = required_arg(
                &mut args,
                "usage: unicode-pdf-cli emit-layout-pdf-breaks <utf8-file> <breaks.txt> <out.pdf> <font.ttf> [font.ttf ...]",
            )?;
            let fonts: Vec<String> = args.collect();
            if fonts.is_empty() {
                return Err(
                    "usage: unicode-pdf-cli emit-layout-pdf-breaks <utf8-file> <breaks.txt> <out.pdf> <font.ttf> [font.ttf ...]"
                        .to_owned(),
                );
            }
            let break_offsets = read_break_offsets(&breaks)?;
            emit_layout_pdf_with_breaks(&fonts, &text, &output, &break_offsets)
        }
        "help" | "--help" | "-h" => {
            print_help();
            Ok(())
        }
        other => Err(format!("unknown command {other:?}; run with --help")),
    }
}

fn required_arg(args: &mut impl Iterator<Item = String>, usage: &str) -> Result<String, String> {
    args.next().ok_or_else(|| usage.to_owned())
}

fn ensure_no_more_args(args: &mut impl Iterator<Item = String>, usage: &str) -> Result<(), String> {
    if args.next().is_some() {
        Err(usage.to_owned())
    } else {
        Ok(())
    }
}

fn inspect(path: &str) -> Result<(), String> {
    let text =
        fs::read_to_string(path).map_err(|error| format!("failed to read {path}: {error}"))?;
    println!("file: {path}");
    println!("utf8_bytes: {}", text.len());
    println!("unicode_scalars: {}", text.chars().count());
    println!("utf16_code_units: {}", text.encode_utf16().count());
    println!();

    for (byte_offset, ch) in text.char_indices() {
        let escaped: String = ch.escape_default().collect();
        println!("byte={byte_offset:>6} U+{:04X} {escaped}", u32::from(ch));
    }
    Ok(())
}

fn shape(font_path: &str, text_path: &str) -> Result<(), String> {
    let font =
        fs::read(font_path).map_err(|error| format!("failed to read font {font_path}: {error}"))?;
    let text = fs::read_to_string(text_path)
        .map_err(|error| format!("failed to read {text_path}: {error}"))?;
    let engine = HarfBuzzShaper::new().map_err(|error| error.to_string())?;
    let output = engine
        .shape(&font, &text, ShapeOptions::new(FontId(1)))
        .map_err(|error| error.to_string())?;

    println!("font: {font_path}");
    println!("text: {text_path}");
    println!("upem: {}", output.units_per_em);
    println!("direction: {:?}", output.run.direction);
    println!("units: {}", output.run.units.len());
    println!("round_trip: {}", output.run.extracted_text() == text);
    println!();

    for (index, unit) in output.run.units.iter().enumerate() {
        let source = unit.source_range.as_ref().map_or_else(
            || "-".to_owned(),
            |range| format!("{}..{}", range.start(), range.end()),
        );
        println!(
            "unit={index:>4} source={source:>12} glyphs={:>2} unicode={:?}",
            unit.glyphs.len(),
            unit.unicode
        );
    }
    Ok(())
}

fn synthesize_font(font_path: &str, text_path: &str, output_path: &str) -> Result<(), String> {
    let font =
        fs::read(font_path).map_err(|error| format!("failed to read font {font_path}: {error}"))?;
    let text = fs::read_to_string(text_path)
        .map_err(|error| format!("failed to read {text_path}: {error}"))?;
    let engine = HarfBuzzShaper::new().map_err(|error| error.to_string())?;
    let output = engine
        .shape(&font, &text, ShapeOptions::new(FontId(1)))
        .map_err(|error| error.to_string())?;

    let mut allocator = CidAllocator::new();
    for unit in &output.run.units {
        allocator
            .get_or_allocate(unit)
            .map_err(|error| error.to_string())?;
    }

    let synthetic = synthesize_truetype_composites(&font, allocator.entries())
        .map_err(|error| error.to_string())?;
    fs::write(output_path, &synthetic.bytes)
        .map_err(|error| format!("failed to write {output_path}: {error}"))?;

    println!("wrote: {output_path}");
    println!("source_font_bytes: {}", font.len());
    println!("synthetic_font_bytes: {}", synthetic.bytes.len());
    println!("base_glyphs: {}", synthetic.base_glyph_count);
    println!("logical_units: {}", output.run.units.len());
    println!("unique_cids: {}", allocator.entries().len());
    println!("synthetic_glyphs: {}", synthetic.synthetic_glyphs.len());
    println!("round_trip: {}", output.run.extracted_text() == text);
    Ok(())
}

fn dump_layout_geometry(
    font_paths: &[String],
    text_path: &str,
    output_path: &str,
) -> Result<(), String> {
    let text = fs::read_to_string(text_path)
        .map_err(|error| format!("failed to read {text_path}: {error}"))?;
    let mut layout_fonts = Vec::with_capacity(font_paths.len());
    for (index, path) in font_paths.iter().enumerate() {
        let bytes =
            fs::read(path).map_err(|error| format!("failed to read font {path}: {error}"))?;
        let raw_id = u32::try_from(index + 1).map_err(|_| "too many fallback fonts".to_owned())?;
        layout_fonts.push(
            LayoutFont::new(FontId(raw_id), font_name_from_path(path), bytes)
                .map_err(|error| error.to_string())?,
        );
    }
    let font_set = FontSet::new(layout_fonts).map_err(|error| error.to_string())?;
    let shaper = HarfBuzzShaper::new().map_err(|error| error.to_string())?;
    let bidi = FriBidiResolver::new().map_err(|error| error.to_string())?;
    let options = LayoutOptions::default();
    let layout = layout_document(&text, &font_set, &shaper, &bidi, options)
        .map_err(|error| error.to_string())?;
    let geometry = GeometryIndex::from_layout(&layout);
    let mut json = String::from("{\n  \"page_width\": ");
    let _ = write!(json, "{:.4}", options.page_width);
    json.push_str(",\n  \"page_height\": ");
    let _ = write!(json, "{:.4}", options.page_height);
    json.push_str(",\n  \"page_count\": ");
    json.push_str(&layout.page_count.to_string());
    json.push_str(",\n  \"line_count\": ");
    json.push_str(&geometry.lines.len().to_string());
    json.push_str(",\n  \"units\": [\n");
    for (index, unit) in geometry.units.iter().enumerate() {
        let range = unit
            .source_range
            .as_ref()
            .expect("geometry units are source mapped");
        let comma = if index + 1 == geometry.units.len() {
            ""
        } else {
            ","
        };
        let _ = writeln!(
            json,
            "    {{\"page\":{},\"source_start\":{},\"source_end\":{},\"unicode\":\"{}\",\"x0\":{:.4},\"y0\":{:.4},\"x1\":{:.4},\"y1\":{:.4},\"font_id\":{}}}{comma}",
            unit.page_index,
            range.start(),
            range.end(),
            json_escape(&unit.unicode),
            unit.rect.x0,
            unit.rect.y0,
            unit.rect.x1,
            unit.rect.y1,
            unit.font_id.0,
        );
    }
    json.push_str("  ],\n  \"lines\": [\n");
    for (index, line) in geometry.lines.iter().enumerate() {
        let comma = if index + 1 == geometry.lines.len() {
            ""
        } else {
            ","
        };
        let _ = writeln!(
            json,
            "    {{\"page\":{},\"source_start\":{},\"source_end\":{},\"baseline_y\":{:.4},\"x0\":{:.4},\"y0\":{:.4},\"x1\":{:.4},\"y1\":{:.4}}}{comma}",
            line.page_index,
            line.source_start,
            line.source_end,
            line.baseline_y,
            line.rect.x0,
            line.rect.y0,
            line.rect.x1,
            line.rect.y1,
        );
    }
    json.push_str("  ]\n}\n");
    fs::write(output_path, json)
        .map_err(|error| format!("failed to write {output_path}: {error}"))?;
    println!("wrote: {output_path}");
    println!("geometry_units: {}", geometry.units.len());
    println!("geometry_lines: {}", geometry.lines.len());
    println!("pages: {}", layout.page_count);
    Ok(())
}

fn read_break_offsets(path: &str) -> Result<Vec<usize>, String> {
    let contents =
        fs::read_to_string(path).map_err(|error| format!("failed to read {path}: {error}"))?;
    let mut offsets = Vec::new();
    for (line_index, line) in contents.lines().enumerate() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let offset = trimmed.parse::<usize>().map_err(|error| {
            format!(
                "invalid byte offset on {}:{} ({trimmed:?}): {error}",
                path,
                line_index + 1
            )
        })?;
        offsets.push(offset);
    }
    offsets.sort_unstable();
    offsets.dedup();
    Ok(offsets)
}

fn json_escape(text: &str) -> String {
    let mut output = String::new();
    for ch in text.chars() {
        match ch {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            ch if ch <= '\u{001F}' => {
                let _ = write!(output, "\\u{:04X}", u32::from(ch));
            }
            ch => output.push(ch),
        }
    }
    output
}

#[allow(clippy::too_many_lines)]
fn emit_layout_pdf(
    font_paths: &[String],
    text_path: &str,
    output_path: &str,
) -> Result<(), String> {
    emit_layout_pdf_with_breaks(font_paths, text_path, output_path, &[])
}

#[allow(clippy::too_many_lines)]
fn emit_layout_pdf_with_breaks(
    font_paths: &[String],
    text_path: &str,
    output_path: &str,
    break_opportunities: &[usize],
) -> Result<(), String> {
    let text = fs::read_to_string(text_path)
        .map_err(|error| format!("failed to read {text_path}: {error}"))?;
    let mut layout_fonts = Vec::with_capacity(font_paths.len());
    for (index, path) in font_paths.iter().enumerate() {
        let bytes =
            fs::read(path).map_err(|error| format!("failed to read font {path}: {error}"))?;
        let raw_id = u32::try_from(index + 1).map_err(|_| "too many fallback fonts".to_owned())?;
        layout_fonts.push(
            LayoutFont::new(FontId(raw_id), font_name_from_path(path), bytes)
                .map_err(|error| error.to_string())?,
        );
    }
    let font_set = FontSet::new(layout_fonts).map_err(|error| error.to_string())?;
    let shaper = HarfBuzzShaper::new().map_err(|error| error.to_string())?;
    let bidi = FriBidiResolver::new().map_err(|error| error.to_string())?;
    let layout_options = LayoutOptions::default();
    let layout = layout_document_with_break_opportunities(
        &text,
        break_opportunities,
        &font_set,
        &shaper,
        &bidi,
        layout_options,
    )
    .map_err(|error| error.to_string())?;

    let mut allocators: Vec<CidAllocator> = font_set
        .fonts()
        .iter()
        .map(|_| CidAllocator::new())
        .collect();
    let mut plans = Vec::with_capacity(layout.runs.len());
    for run in &layout.runs {
        plans.push(
            plan_text_run(&run.units, &mut allocators[run.font_index])
                .map_err(|error| error.to_string())?,
        );
    }

    let mut original_to_resource = vec![None; font_set.fonts().len()];
    let mut synthesized = Vec::new();
    let mut resource_upem = Vec::new();
    let mut resource_names = Vec::new();
    for (font_index, font) in font_set.fonts().iter().enumerate() {
        if allocators[font_index].entries().is_empty() {
            continue;
        }
        let empty_shape = shaper
            .shape(&font.bytes, "", ShapeOptions::new(font.id))
            .map_err(|error| error.to_string())?;
        let synthetic =
            synthesize_truetype_composites(&font.bytes, allocators[font_index].entries())
                .map_err(|error| error.to_string())?;
        let resource_index = synthesized.len();
        original_to_resource[font_index] = Some(resource_index);
        synthesized.push(synthetic);
        resource_upem.push(empty_shape.units_per_em);
        resource_names.push(format!("UPDFAB+{}", pdf_safe_font_name(&font.name)));
    }

    let embedded: Vec<EmbeddedType0Font<'_>> = synthesized
        .iter()
        .enumerate()
        .map(|(index, font)| EmbeddedType0Font {
            font,
            units_per_em: resource_upem[index],
            base_font_name: resource_names[index].clone(),
        })
        .collect();
    let placed: Vec<DocumentPlacedTextRun<'_>> = layout
        .runs
        .iter()
        .zip(&plans)
        .map(|(run, plan)| {
            let font_index = original_to_resource[run.font_index]
                .expect("every laid-out font must have an allocated resource");
            DocumentPlacedTextRun {
                plan,
                font_index,
                page_index: run.page_index,
                run_origin_x: run.run_origin_x,
                baseline_y: run.baseline_y,
                font_size: run.font_size,
                direction: run.direction,
                language: Some(run.language),
                paragraph_index: run.paragraph_index,
            }
        })
        .collect();
    let options = Type0DocumentOptions {
        page_width: layout_options.page_width,
        page_height: layout_options.page_height,
        tagged: true,
        document_language: document_language_from_layout(&layout.runs).to_owned(),
        actual_text: ActualTextPolicy::Off,
        paragraphs: layout
            .paragraphs
            .iter()
            .map(|paragraph| DocumentParagraphText {
                paragraph_index: paragraph.paragraph_index,
                unicode: paragraph.unicode.clone(),
                terminated_by_newline: paragraph.terminated_by_newline,
            })
            .collect(),
        paragraph_text: paragraph_text_policy_from_env()?,
    };
    let pdf = build_type0_document_pdf(&embedded, layout.page_count, &placed, &options)
        .map_err(|error| error.to_string())?;
    fs::write(output_path, &pdf)
        .map_err(|error| format!("failed to write {output_path}: {error}"))?;

    println!("wrote: {output_path}");
    println!("pdf_bytes: {}", pdf.len());
    println!("fallback_fonts: {}", font_set.fonts().len());
    println!("embedded_fonts: {}", embedded.len());
    println!("semantic_runs: {}", layout.runs.len());
    println!("visual_lines: {}", layout.line_count);
    println!("logical_paragraphs: {}", layout.paragraphs.len());
    println!("pages: {}", layout.page_count);
    println!(
        "round_trip_units: {}",
        logical_layout_text(&layout.runs) == text.replace('\n', "")
    );
    println!("round_trip_paragraphs: {}", layout.logical_text() == text);
    Ok(())
}

fn paragraph_text_policy_from_env() -> Result<ParagraphTextPolicy, String> {
    match std::env::var("UNICODE_PDF_PARAGRAPH_TEXT_POLICY")
        .unwrap_or_else(|_| "structure".to_owned())
        .as_str()
    {
        "off" => Ok(ParagraphTextPolicy::Off),
        "structure" => Ok(ParagraphTextPolicy::StructureActualText),
        "page-fragment" => Ok(ParagraphTextPolicy::PageFragmentActualText),
        "structure-and-page-fragment" => Ok(ParagraphTextPolicy::StructureAndPageFragment),
        other => Err(format!(
            "invalid UNICODE_PDF_PARAGRAPH_TEXT_POLICY={other:?}; expected off, structure, page-fragment, or structure-and-page-fragment"
        )),
    }
}

fn logical_layout_text(runs: &[unicode_pdf_layout::LayoutRun]) -> String {
    runs.iter()
        .map(unicode_pdf_layout::LayoutRun::text)
        .collect()
}

fn document_language_from_layout(runs: &[unicode_pdf_layout::LayoutRun]) -> &'static str {
    let mut found = None;
    for run in runs {
        if run.language == "und" {
            continue;
        }
        match found {
            None => found = Some(run.language),
            Some(existing) if existing == run.language => {}
            Some(_) => return "und",
        }
    }
    found.unwrap_or("und")
}

fn font_name_from_path(path: &str) -> String {
    std::path::Path::new(path)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("UnicodePdfFont")
        .to_owned()
}

fn pdf_safe_font_name(name: &str) -> String {
    name.chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '+') {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

#[derive(Clone, Copy, Debug)]
struct PlannedRunMeta {
    direction: TextDirection,
    visual_order: usize,
    paragraph_index: u32,
    language: &'static str,
}

fn emit_pdf(font_path: &str, text_path: &str, output_path: &str) -> Result<(), String> {
    let font =
        fs::read(font_path).map_err(|error| format!("failed to read font {font_path}: {error}"))?;
    let text = fs::read_to_string(text_path)
        .map_err(|error| format!("failed to read {text_path}: {error}"))?;
    let shaper = HarfBuzzShaper::new().map_err(|error| error.to_string())?;
    let bidi = FriBidiResolver::new().map_err(|error| error.to_string())?;
    let mut allocator = CidAllocator::new();
    let (plans, metadata, units_per_em) =
        plan_bidi_document(&font, &text, &shaper, &bidi, &mut allocator)?;

    let synthetic = synthesize_truetype_composites(&font, allocator.entries())
        .map_err(|error| error.to_string())?;
    let options = Type0PdfOptions {
        base_font_name: pdf_font_name_from_path(font_path),
        font_size: 18.0,
        document_language: document_language(&metadata).to_owned(),
        actual_text: ActualTextPolicy::ComplexUnits,
        ..Type0PdfOptions::default()
    };
    let origins = layout_run_origins(&plans, &metadata, units_per_em, &options);
    let placements = place_runs(&plans, &metadata, &origins, &options);
    let pdf = build_type0_single_page_pdf(&synthetic, units_per_em, &placements, &options)
        .map_err(|error| error.to_string())?;
    fs::write(output_path, &pdf)
        .map_err(|error| format!("failed to write {output_path}: {error}"))?;

    println!("wrote: {output_path}");
    println!("pdf_bytes: {}", pdf.len());
    println!("source_font_bytes: {}", font.len());
    println!("embedded_font_bytes: {}", synthetic.bytes.len());
    println!("semantic_runs: {}", plans.len());
    println!("unique_cids: {}", allocator.entries().len());
    println!("synthetic_glyphs: {}", synthetic.synthetic_glyphs.len());
    println!("tagged: {}", options.tagged);
    println!("document_language: {}", options.document_language);
    Ok(())
}

fn plan_bidi_document(
    font: &[u8],
    text: &str,
    shaper: &HarfBuzzShaper,
    bidi: &FriBidiResolver,
    allocator: &mut CidAllocator,
) -> Result<(Vec<TextPlan>, Vec<PlannedRunMeta>, u32), String> {
    let mut plans = Vec::new();
    let mut metadata = Vec::new();
    let mut units_per_em = None;

    for (line_index, line) in text.lines().enumerate() {
        let paragraph_index = u32::try_from(line_index)
            .map_err(|_| "too many lines for single-page development layout".to_owned())?;
        let paragraph = bidi.resolve(line).map_err(|error| error.to_string())?;
        for bidi_run in paragraph.runs {
            let run_text = line
                .get(bidi_run.source_range.start()..bidi_run.source_range.end())
                .ok_or_else(|| "BiDi run is not on UTF-8 boundaries".to_owned())?;
            let output = shaper
                .shape(
                    font,
                    run_text,
                    ShapeOptions::new(FontId(1)).with_direction(bidi_run.direction),
                )
                .map_err(|error| error.to_string())?;
            update_units_per_em(&mut units_per_em, output.units_per_em)?;
            plans.push(
                plan_text_run(&output.run.units, allocator).map_err(|error| error.to_string())?,
            );
            metadata.push(PlannedRunMeta {
                direction: bidi_run.direction,
                visual_order: bidi_run.visual_order,
                paragraph_index,
                language: detect_script_language(run_text),
            });
        }
    }

    if units_per_em.is_none() {
        let output = shaper
            .shape(font, "", ShapeOptions::new(FontId(1)))
            .map_err(|error| error.to_string())?;
        units_per_em = Some(output.units_per_em);
    }
    let units_per_em = units_per_em.ok_or_else(|| "font units-per-em unavailable".to_owned())?;
    Ok((plans, metadata, units_per_em))
}

fn layout_run_origins(
    plans: &[TextPlan],
    metadata: &[PlannedRunMeta],
    units_per_em: u32,
    options: &Type0PdfOptions,
) -> Vec<f64> {
    let scale = options.font_size / f64::from(units_per_em);
    let left_margin = 54.0_f64;
    let mut origins = vec![0.0_f64; plans.len()];
    let max_paragraph = metadata
        .iter()
        .map(|meta| meta.paragraph_index)
        .max()
        .unwrap_or(0);

    for paragraph_index in 0..=max_paragraph {
        let mut visual_runs: Vec<usize> = metadata
            .iter()
            .enumerate()
            .filter(|(_, meta)| meta.paragraph_index == paragraph_index)
            .map(|(index, _)| index)
            .collect();
        visual_runs.sort_by_key(|index| metadata[*index].visual_order);
        let mut cursor_x = left_margin;
        for index in visual_runs {
            origins[index] = cursor_x - f64::from(plans[index].min_visual_x()) * scale;
            cursor_x += f64::from(plans[index].visual_width()) * scale;
        }
    }
    origins
}

fn place_runs<'a>(
    plans: &'a [TextPlan],
    metadata: &[PlannedRunMeta],
    origins: &[f64],
    options: &Type0PdfOptions,
) -> Vec<PlacedTextRun<'a>> {
    let top_margin = 72.0_f64;
    let line_height = options.font_size * 1.6;
    plans
        .iter()
        .enumerate()
        .map(|(index, plan)| {
            let meta = metadata[index];
            let baseline_y =
                options.page_height - top_margin - f64::from(meta.paragraph_index) * line_height;
            PlacedTextRun {
                plan,
                run_origin_x: origins[index],
                baseline_y,
                direction: meta.direction,
                language: Some(meta.language),
                paragraph_index: meta.paragraph_index,
            }
        })
        .collect()
}

fn update_units_per_em(slot: &mut Option<u32>, value: u32) -> Result<(), String> {
    if let Some(expected) = *slot {
        if expected != value {
            return Err("shaping backend returned inconsistent units-per-em".to_owned());
        }
    } else {
        *slot = Some(value);
    }
    Ok(())
}

fn detect_script_language(text: &str) -> &'static str {
    let mut has_latin = false;
    for ch in text.chars() {
        let code = u32::from(ch);
        if (0x1780..=0x17FF).contains(&code) {
            return "und-Khmr";
        }
        if (0x0600..=0x06FF).contains(&code)
            || (0x0750..=0x077F).contains(&code)
            || (0x08A0..=0x08FF).contains(&code)
        {
            return "und-Arab";
        }
        if (0x0900..=0x097F).contains(&code) {
            return "und-Deva";
        }
        if ch.is_ascii_alphabetic() || (0x00C0..=0x024F).contains(&code) {
            has_latin = true;
        }
    }
    if has_latin {
        "und-Latn"
    } else {
        "und"
    }
}

fn document_language(metadata: &[PlannedRunMeta]) -> &'static str {
    let mut found = None;
    for meta in metadata {
        if meta.language == "und" {
            continue;
        }
        match found {
            None => found = Some(meta.language),
            Some(existing) if existing == meta.language => {}
            Some(_) => return "und",
        }
    }
    found.unwrap_or("und")
}

fn pdf_font_name_from_path(font_path: &str) -> String {
    let stem = std::path::Path::new(font_path)
        .file_stem()
        .and_then(|value| value.to_str())
        .unwrap_or("UnicodePdfSynthetic");
    format!("UPDFAB+{stem}")
}

fn print_help() {
    println!("unicode-pdf development CLI");
    println!();
    println!("USAGE:");
    println!("  unicode-pdf-cli inspect <utf8-file>");
    println!("  unicode-pdf-cli shape <font.ttf> <utf8-file>");
    println!("  unicode-pdf-cli synthesize-font <font.ttf> <utf8-file> <out.ttf>");
    println!("  unicode-pdf-cli emit-pdf <font.ttf> <utf8-file> <out.pdf>");
    println!(
        "  unicode-pdf-cli emit-layout-pdf <utf8-file> <out.pdf> <font.ttf> [font.ttf ...]
  unicode-pdf-cli dump-layout-geometry <utf8-file> <out.json> <font.ttf> [font.ttf ...]"
    );
}
