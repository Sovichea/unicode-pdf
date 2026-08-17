//! Multi-font fallback and paragraph layout for `unicode-pdf`.
//!
//! This layer keeps semantic source order separate from visual geometry. Font
//! fallback is based on actual `cmap` coverage. Soft line wrapping never edits
//! the logical Unicode stored in [`unicode_pdf_core::LogicalPdfUnit`].

use std::fmt;
use std::ops::Range;

use unicode_pdf_bidi::BidiResolver;
use unicode_pdf_core::{FontId, LogicalPdfUnit, SourceRange, TextDirection};
use unicode_pdf_font::FontCoverage;
use unicode_pdf_shape::{ShapeOptions, TextShaper};

/// One font available to the fallback resolver.
#[derive(Clone, Debug)]
pub struct LayoutFont {
    /// Stable font identity written into logical PDF units.
    pub id: FontId,
    /// Human-readable name used for diagnostics and PDF resource naming.
    pub name: String,
    /// Complete font bytes passed to the shaping and font-synthesis layers.
    pub bytes: Vec<u8>,
    /// Parsed Unicode `cmap` coverage.
    pub coverage: FontCoverage,
}

impl LayoutFont {
    /// Creates a layout font and parses its Unicode coverage.
    ///
    /// # Errors
    ///
    /// Returns [`LayoutError::Font`] if the font has no supported Unicode cmap.
    pub fn new(id: FontId, name: impl Into<String>, bytes: Vec<u8>) -> Result<Self, LayoutError> {
        let coverage =
            FontCoverage::parse(&bytes).map_err(|error| LayoutError::Font(error.to_string()))?;
        Ok(Self {
            id,
            name: name.into(),
            bytes,
            coverage,
        })
    }
}

/// Ordered fallback font set. The first covering font wins.
#[derive(Clone, Debug)]
pub struct FontSet {
    fonts: Vec<LayoutFont>,
}

impl FontSet {
    /// Creates a non-empty ordered fallback set.
    ///
    /// # Errors
    ///
    /// Returns [`LayoutError::NoFonts`] when no fonts are supplied or when IDs
    /// are duplicated.
    pub fn new(fonts: Vec<LayoutFont>) -> Result<Self, LayoutError> {
        if fonts.is_empty() {
            return Err(LayoutError::NoFonts);
        }
        for (index, font) in fonts.iter().enumerate() {
            if fonts[..index].iter().any(|other| other.id == font.id) {
                return Err(LayoutError::DuplicateFontId(font.id));
            }
        }
        Ok(Self { fonts })
    }

    /// Returns fonts in fallback priority order.
    #[must_use]
    pub fn fonts(&self) -> &[LayoutFont] {
        &self.fonts
    }

    /// Returns a font by its stable identifier.
    #[must_use]
    pub fn font_by_id(&self, id: FontId) -> Option<&LayoutFont> {
        self.fonts.iter().find(|font| font.id == id)
    }

    fn choose_for_char(&self, ch: char, preferred: Option<usize>) -> Option<usize> {
        if let Some(index) = preferred {
            if self.fonts[index].coverage.covers(ch) {
                return Some(index);
            }
        }
        self.fonts.iter().position(|font| font.coverage.covers(ch))
    }
}

/// Page and paragraph metrics for the development layout engine.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LayoutOptions {
    /// Page width in PDF points.
    pub page_width: f64,
    /// Page height in PDF points.
    pub page_height: f64,
    /// Left margin in PDF points.
    pub margin_left: f64,
    /// Right margin in PDF points.
    pub margin_right: f64,
    /// Top margin in PDF points.
    pub margin_top: f64,
    /// Bottom margin in PDF points.
    pub margin_bottom: f64,
    /// Font size in PDF points.
    pub font_size: f64,
    /// Baseline-to-baseline line advance in PDF points.
    pub line_height: f64,
}

impl Default for LayoutOptions {
    fn default() -> Self {
        Self {
            page_width: 595.0,
            page_height: 842.0,
            margin_left: 54.0,
            margin_right: 54.0,
            margin_top: 54.0,
            margin_bottom: 54.0,
            font_size: 14.0,
            line_height: 21.0,
        }
    }
}

impl LayoutOptions {
    /// Returns available line width in PDF points.
    #[must_use]
    pub fn content_width(self) -> f64 {
        self.page_width - self.margin_left - self.margin_right
    }

    fn validate(self) -> Result<(), LayoutError> {
        let metrics = [
            self.page_width,
            self.page_height,
            self.margin_left,
            self.margin_right,
            self.margin_top,
            self.margin_bottom,
            self.font_size,
            self.line_height,
        ];
        if metrics
            .iter()
            .any(|value| !value.is_finite() || *value < 0.0)
            || self.page_width <= 0.0
            || self.page_height <= 0.0
            || self.font_size <= 0.0
            || self.line_height <= 0.0
            || self.content_width() <= 0.0
            || self.margin_top + self.margin_bottom + self.line_height > self.page_height
        {
            return Err(LayoutError::InvalidOptions);
        }
        Ok(())
    }
}

/// One shaped semantic run placed on a physical page.
#[derive(Clone, Debug)]
pub struct LayoutRun {
    /// Font index in [`FontSet::fonts`].
    pub font_index: usize,
    /// Font units-per-em for geometry conversion.
    pub units_per_em: u32,
    /// Logical PDF units in original source order.
    pub units: Vec<LogicalPdfUnit>,
    /// Resolved run direction.
    pub direction: TextDirection,
    /// BCP 47-ish script language tag used by the tagged-PDF layer.
    pub language: &'static str,
    /// Logical paragraph number.
    pub paragraph_index: u32,
    /// Physical page index, zero based.
    pub page_index: u32,
    /// PDF X coordinate corresponding to shaping run X = 0.
    pub run_origin_x: f64,
    /// PDF baseline Y coordinate.
    pub baseline_y: f64,
    /// Font size in PDF points.
    pub font_size: f64,
}

/// One semantic paragraph retained independently from its visual lines.
///
/// `unicode` is copied exactly from the caller's UTF-8 source, excluding the
/// hard paragraph delimiter itself. Soft wrapping and pagination never modify
/// this string.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalParagraph {
    /// Stable paragraph identifier referenced by [`LayoutRun::paragraph_index`].
    pub paragraph_index: u32,
    /// UTF-8 byte range occupied by this paragraph in the original source.
    pub source_range: SourceRange,
    /// Exact logical Unicode of the paragraph before layout.
    pub unicode: String,
    /// Whether the source contained a hard paragraph delimiter after this
    /// paragraph. The delimiter is source semantics, unlike a soft visual wrap.
    pub terminated_by_newline: bool,
}

impl LayoutRun {
    /// Reconstructs this run's exact logical Unicode.
    #[must_use]
    pub fn text(&self) -> String {
        self.units
            .iter()
            .map(|unit| unit.unicode.as_str())
            .collect()
    }
}

/// One laid out document.
#[derive(Clone, Debug)]
pub struct LayoutDocument {
    /// Physical page count. Always at least one.
    pub page_count: u32,
    /// Semantic runs in logical source order.
    pub runs: Vec<LayoutRun>,
    /// Exact logical paragraphs in source order.
    pub paragraphs: Vec<LogicalParagraph>,
    /// Number of visual soft-wrapped lines.
    pub line_count: usize,
}

impl LayoutDocument {
    /// Reconstructs the exact logical UTF-8 text represented by the semantic
    /// paragraph model, including hard source newlines but excluding all soft
    /// visual wraps and page breaks.
    #[must_use]
    pub fn logical_text(&self) -> String {
        let mut output = String::new();
        for paragraph in &self.paragraphs {
            output.push_str(&paragraph.unicode);
            if paragraph.terminated_by_newline {
                output.push('\n');
            }
        }
        output
    }
}

/// Shapes, wraps, and paginates a UTF-8 document using font fallback.
///
/// Hard `\n` characters delimit logical paragraphs. Soft wrapping only changes
/// page geometry; no Unicode is inserted into the logical units.
///
/// # Errors
///
/// Returns [`LayoutError`] if `BiDi` analysis/shaping fails, a scalar is not
/// covered by any fallback font, or geometry is invalid.
pub fn layout_document<S: TextShaper, B: BidiResolver>(
    text: &str,
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
    options: LayoutOptions,
) -> Result<LayoutDocument, LayoutError> {
    layout_document_with_break_opportunities(text, &[], fonts, shaper, bidi, options)
}

/// Shapes, wraps, and paginates a UTF-8 document using caller-supplied soft
/// line-break opportunities.
///
/// `break_opportunities` contains UTF-8 byte offsets in the original source
/// after which a visual line may break. The offsets are layout metadata only:
/// no Unicode scalar, whitespace, or newline is inserted into the logical text.
/// Default whitespace opportunities are retained and merged with these offsets.
/// This is intended for language-aware boundary providers such as ICU4X or a
/// Khmer word segmenter.
///
/// # Errors
///
/// Returns [`LayoutError::InvalidBreakOpportunity`] if an offset lies outside
/// the source or is not on a UTF-8 boundary, in addition to the normal layout
/// errors documented by [`layout_document`].
pub fn layout_document_with_break_opportunities<S: TextShaper, B: BidiResolver>(
    text: &str,
    break_opportunities: &[usize],
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
    options: LayoutOptions,
) -> Result<LayoutDocument, LayoutError> {
    options.validate()?;
    for &offset in break_opportunities {
        if offset > text.len() || !text.is_char_boundary(offset) {
            return Err(LayoutError::InvalidBreakOpportunity(offset));
        }
    }
    let mut runs = Vec::new();
    let mut paragraphs = Vec::new();
    let mut page_index = 0_u32;
    let mut baseline_y = options.page_height - options.margin_top - options.font_size;
    let mut line_count = 0_usize;
    let mut paragraph_index = 0_u32;
    let mut paragraph_start = 0_usize;

    for paragraph_with_newline in text.split_inclusive('\n') {
        let has_newline = paragraph_with_newline.ends_with('\n');
        let paragraph = paragraph_with_newline
            .strip_suffix('\n')
            .unwrap_or(paragraph_with_newline);
        let paragraph_end = paragraph_start + paragraph.len();
        let source_range = SourceRange::new(paragraph_start, paragraph_end)
            .map_err(|_| LayoutError::InvalidUtf8Boundary)?;
        paragraphs.push(LogicalParagraph {
            paragraph_index,
            source_range,
            unicode: paragraph.to_owned(),
            terminated_by_newline: has_newline,
        });
        let local_breaks: Vec<usize> = break_opportunities
            .iter()
            .copied()
            .filter(|offset| *offset > paragraph_start && *offset <= paragraph_end)
            .map(|offset| offset - paragraph_start)
            .collect();
        let line_ranges = wrap_paragraph(paragraph, &local_breaks, fonts, shaper, bidi, options)?;

        if line_ranges.is_empty() {
            advance_line(&mut page_index, &mut baseline_y, options);
            line_count += 1;
        } else {
            for range in line_ranges {
                if baseline_y < options.margin_bottom {
                    page_index = page_index.saturating_add(1);
                    baseline_y = options.page_height - options.margin_top - options.font_size;
                }
                let line = &paragraph[range.clone()];
                let mut line_runs = shape_visual_line(
                    line,
                    paragraph_start + range.start,
                    paragraph_index,
                    page_index,
                    baseline_y,
                    fonts,
                    shaper,
                    bidi,
                    options,
                )?;
                runs.append(&mut line_runs);
                line_count += 1;
                baseline_y -= options.line_height;
            }
        }

        paragraph_start += paragraph_with_newline.len();
        paragraph_index = paragraph_index.saturating_add(1);
        if has_newline && baseline_y < options.margin_bottom {
            page_index = page_index.saturating_add(1);
            baseline_y = options.page_height - options.margin_top - options.font_size;
        }
    }

    // `split_inclusive` yields no item for an empty string.
    if text.is_empty() {
        line_count = 1;
        paragraphs.push(LogicalParagraph {
            paragraph_index: 0,
            source_range: SourceRange::new(0, 0).map_err(|_| LayoutError::InvalidUtf8Boundary)?,
            unicode: String::new(),
            terminated_by_newline: false,
        });
    }

    Ok(LayoutDocument {
        page_count: page_index.saturating_add(1),
        runs,
        paragraphs,
        line_count,
    })
}

fn advance_line(page_index: &mut u32, baseline_y: &mut f64, options: LayoutOptions) {
    *baseline_y -= options.line_height;
    if *baseline_y < options.margin_bottom {
        *page_index = page_index.saturating_add(1);
        *baseline_y = options.page_height - options.margin_top - options.font_size;
    }
}

fn wrap_paragraph<S: TextShaper, B: BidiResolver>(
    paragraph: &str,
    break_opportunities: &[usize],
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
    options: LayoutOptions,
) -> Result<Vec<Range<usize>>, LayoutError> {
    if paragraph.is_empty() {
        return Ok(Vec::new());
    }
    let segments = break_segments_with_opportunities(paragraph, break_opportunities)?;
    let mut lines = Vec::new();
    let mut line_start = 0_usize;
    let mut line_end = 0_usize;

    for segment in segments {
        let candidate_end = segment.end;
        let candidate = &paragraph[line_start..candidate_end];
        let width = measure_line(candidate, fonts, shaper, bidi, options.font_size)?;
        if line_end > line_start && width > options.content_width() {
            lines.push(line_start..line_end);
            line_start = segment.start;
            line_end = segment.end;
            if measure_line(
                &paragraph[line_start..line_end],
                fonts,
                shaper,
                bidi,
                options.font_size,
            )? > options.content_width()
            {
                let split = split_oversize_segment(
                    paragraph,
                    line_start..line_end,
                    fonts,
                    shaper,
                    bidi,
                    options,
                )?;
                if let Some((last, preceding)) = split.split_last() {
                    lines.extend_from_slice(preceding);
                    line_start = last.start;
                    line_end = last.end;
                }
            }
        } else {
            line_end = candidate_end;
        }
    }
    if line_end > line_start {
        lines.push(line_start..line_end);
    }
    Ok(lines)
}

fn break_segments(text: &str) -> Vec<Range<usize>> {
    let mut ranges = Vec::new();
    let mut start = 0_usize;
    let mut in_whitespace = false;
    for (offset, ch) in text.char_indices() {
        let whitespace = ch.is_whitespace();
        if in_whitespace && !whitespace {
            ranges.push(start..offset);
            start = offset;
        }
        in_whitespace = whitespace;
    }
    if start < text.len() {
        ranges.push(start..text.len());
    }
    // Pair each word with following whitespace. This creates natural soft-break
    // opportunities while preserving every source scalar exactly once.
    let mut paired = Vec::new();
    let mut index = 0;
    while index < ranges.len() {
        if !text[ranges[index].clone()].chars().all(char::is_whitespace)
            && index + 1 < ranges.len()
            && text[ranges[index + 1].clone()]
                .chars()
                .all(char::is_whitespace)
        {
            let end = ranges[index + 1].end;
            paired.push(ranges[index].start..end);
            index += 2;
        } else {
            paired.push(ranges[index].clone());
            index += 1;
        }
    }
    paired
}

fn break_segments_with_opportunities(
    text: &str,
    break_opportunities: &[usize],
) -> Result<Vec<Range<usize>>, LayoutError> {
    if text.is_empty() {
        return Ok(Vec::new());
    }

    let mut boundaries: Vec<usize> = break_segments(text)
        .into_iter()
        .map(|range| range.end)
        .collect();
    for &offset in break_opportunities {
        if offset > text.len() || !text.is_char_boundary(offset) {
            return Err(LayoutError::InvalidBreakOpportunity(offset));
        }
        if offset > 0 {
            boundaries.push(offset);
        }
    }
    boundaries.push(text.len());
    boundaries.sort_unstable();
    boundaries.dedup();

    let mut start = 0_usize;
    let mut ranges = Vec::with_capacity(boundaries.len());
    for end in boundaries {
        if end > start {
            ranges.push(start..end);
            start = end;
        }
    }
    Ok(ranges)
}

fn split_oversize_segment<S: TextShaper, B: BidiResolver>(
    paragraph: &str,
    segment: Range<usize>,
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
    options: LayoutOptions,
) -> Result<Vec<Range<usize>>, LayoutError> {
    let mut result = Vec::new();
    let mut start = segment.start;
    let boundaries: Vec<usize> = paragraph[segment.clone()]
        .char_indices()
        .map(|(offset, _)| segment.start + offset)
        .chain(std::iter::once(segment.end))
        .collect();
    let mut last_fit = start;
    for end in boundaries.into_iter().skip(1) {
        let width = measure_line(
            &paragraph[start..end],
            fonts,
            shaper,
            bidi,
            options.font_size,
        )?;
        if width <= options.content_width() || last_fit == start {
            last_fit = end;
            continue;
        }
        result.push(start..last_fit);
        start = last_fit;
        last_fit = end;
    }
    if last_fit > start {
        result.push(start..last_fit);
    }
    Ok(result)
}

fn measure_line<S: TextShaper, B: BidiResolver>(
    text: &str,
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
    font_size: f64,
) -> Result<f64, LayoutError> {
    let spans = shape_line_spans(text, 0, fonts, shaper, bidi)?;
    Ok(spans.iter().map(|span| span.width_points(font_size)).sum())
}

#[allow(clippy::too_many_arguments)]
fn shape_visual_line<S: TextShaper, B: BidiResolver>(
    text: &str,
    global_start: usize,
    paragraph_index: u32,
    page_index: u32,
    baseline_y: f64,
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
    options: LayoutOptions,
) -> Result<Vec<LayoutRun>, LayoutError> {
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let mut spans = shape_line_spans(text, global_start, fonts, shaper, bidi)?;
    let total_width: f64 = spans
        .iter()
        .map(|span| span.width_points(options.font_size))
        .sum();
    let base_direction = bidi
        .resolve(text)
        .map_err(|error| LayoutError::Bidi(error.to_string()))?
        .base_direction;
    let line_left = if base_direction == TextDirection::RightToLeft {
        options.page_width - options.margin_right - total_width
    } else {
        options.margin_left
    };

    let mut visual_indices: Vec<usize> = (0..spans.len()).collect();
    visual_indices.sort_by_key(|index| spans[*index].visual_key);
    let mut cursor = line_left;
    for index in visual_indices {
        let width = spans[index].width_points(options.font_size);
        let min_x = spans[index].min_visual_x();
        let scale = options.font_size / f64::from(spans[index].units_per_em);
        spans[index].origin_x = cursor - f64::from(min_x) * scale;
        cursor += width;
    }

    Ok(spans
        .into_iter()
        .map(|span| LayoutRun {
            font_index: span.font_index,
            units_per_em: span.units_per_em,
            units: span.units,
            direction: span.direction,
            language: span.language,
            paragraph_index,
            page_index,
            run_origin_x: span.origin_x,
            baseline_y,
            font_size: options.font_size,
        })
        .collect())
}

#[derive(Clone, Debug)]
struct ShapedSpan {
    font_index: usize,
    units_per_em: u32,
    units: Vec<LogicalPdfUnit>,
    direction: TextDirection,
    language: &'static str,
    visual_key: (usize, usize),
    origin_x: f64,
}

impl ShapedSpan {
    fn min_visual_x(&self) -> i32 {
        self.units
            .iter()
            .flat_map(|unit| {
                unit.glyphs
                    .iter()
                    .flat_map(|glyph| [glyph.run_x, glyph.run_x.saturating_add(glyph.x_advance)])
            })
            .min()
            .unwrap_or(0)
    }

    fn max_visual_x(&self) -> i32 {
        self.units
            .iter()
            .flat_map(|unit| {
                unit.glyphs
                    .iter()
                    .flat_map(|glyph| [glyph.run_x, glyph.run_x.saturating_add(glyph.x_advance)])
            })
            .max()
            .unwrap_or(0)
    }

    fn width_points(&self, font_size: f64) -> f64 {
        f64::from(self.max_visual_x().saturating_sub(self.min_visual_x())) * font_size
            / f64::from(self.units_per_em)
    }
}

fn shape_line_spans<S: TextShaper, B: BidiResolver>(
    text: &str,
    global_start: usize,
    fonts: &FontSet,
    shaper: &S,
    bidi: &B,
) -> Result<Vec<ShapedSpan>, LayoutError> {
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let paragraph = bidi
        .resolve(text)
        .map_err(|error| LayoutError::Bidi(error.to_string()))?;
    let mut result = Vec::new();
    for bidi_run in paragraph.runs {
        let run_text = text
            .get(bidi_run.source_range.start()..bidi_run.source_range.end())
            .ok_or(LayoutError::InvalidUtf8Boundary)?;
        let fallback_spans = fallback_spans(run_text, fonts)?;
        let span_count = fallback_spans.len();
        for (logical_index, fallback) in fallback_spans.into_iter().enumerate() {
            let font = &fonts.fonts()[fallback.font_index];
            let span_text = run_text
                .get(fallback.range.clone())
                .ok_or(LayoutError::InvalidUtf8Boundary)?;
            let output = shaper
                .shape(
                    &font.bytes,
                    span_text,
                    ShapeOptions::new(font.id).with_direction(bidi_run.direction),
                )
                .map_err(|error| LayoutError::Shape(error.to_string()))?;
            let source_offset = global_start + bidi_run.source_range.start() + fallback.range.start;
            let mut units = output.run.units;
            for unit in &mut units {
                if let Some(range) = &unit.source_range {
                    unit.source_range = Some(
                        SourceRange::new(
                            source_offset + range.start(),
                            source_offset + range.end(),
                        )
                        .map_err(|_| LayoutError::InvalidUtf8Boundary)?,
                    );
                }
            }
            let within_visual = if bidi_run.direction == TextDirection::RightToLeft {
                span_count.saturating_sub(logical_index + 1)
            } else {
                logical_index
            };
            result.push(ShapedSpan {
                font_index: fallback.font_index,
                units_per_em: output.units_per_em,
                units,
                direction: bidi_run.direction,
                language: detect_script_language(span_text),
                visual_key: (bidi_run.visual_order, within_visual),
                origin_x: 0.0,
            });
        }
    }
    Ok(result)
}

#[derive(Clone, Debug)]
struct FallbackSpan {
    font_index: usize,
    range: Range<usize>,
}

fn fallback_spans(text: &str, fonts: &FontSet) -> Result<Vec<FallbackSpan>, LayoutError> {
    if text.is_empty() {
        return Ok(Vec::new());
    }
    let mut spans = Vec::new();
    let mut current_font = None;
    let mut current_start = 0_usize;
    let mut preferred = None;

    for (offset, ch) in text.char_indices() {
        let font_index = fonts
            .choose_for_char(ch, if is_neutral(ch) { preferred } else { None })
            .ok_or(LayoutError::MissingGlyph {
                ch,
                codepoint: u32::from(ch),
            })?;
        if current_font != Some(font_index) {
            if let Some(previous) = current_font {
                spans.push(FallbackSpan {
                    font_index: previous,
                    range: current_start..offset,
                });
            }
            current_font = Some(font_index);
            current_start = offset;
        }
        if !is_neutral(ch) {
            preferred = Some(font_index);
        }
    }
    if let Some(font_index) = current_font {
        spans.push(FallbackSpan {
            font_index,
            range: current_start..text.len(),
        });
    }
    Ok(spans)
}

fn is_neutral(ch: char) -> bool {
    ch.is_whitespace() || ch.is_ascii_punctuation() || ch.is_ascii_digit()
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

/// Layout failures.
#[derive(Clone, Debug, PartialEq)]
pub enum LayoutError {
    /// No fallback fonts were supplied.
    NoFonts,
    /// Font identifiers must be unique.
    DuplicateFontId(FontId),
    /// Font parsing failed.
    Font(String),
    /// `BiDi` resolution failed.
    Bidi(String),
    /// Shaping failed.
    Shape(String),
    /// No supplied fallback font contains a glyph for this scalar.
    MissingGlyph {
        /// Missing Unicode scalar.
        ch: char,
        /// Numeric Unicode scalar value.
        codepoint: u32,
    },
    /// An internal source range was not on a UTF-8 boundary.
    InvalidUtf8Boundary,
    /// A caller-supplied soft line-break byte offset was invalid.
    InvalidBreakOpportunity(usize),
    /// Page/layout metrics are invalid.
    InvalidOptions,
}

impl fmt::Display for LayoutError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoFonts => write!(f, "font fallback set must not be empty"),
            Self::DuplicateFontId(id) => write!(f, "duplicate font ID {}", id.0),
            Self::Font(message) => write!(f, "font error: {message}"),
            Self::Bidi(message) => write!(f, "BiDi error: {message}"),
            Self::Shape(message) => write!(f, "shaping error: {message}"),
            Self::MissingGlyph { ch, codepoint } => {
                write!(f, "no fallback font covers {ch:?} (U+{codepoint:04X})")
            }
            Self::InvalidUtf8Boundary => write!(f, "layout source range is not a UTF-8 boundary"),
            Self::InvalidBreakOpportunity(offset) => write!(
                f,
                "line-break opportunity at byte offset {offset} is not a valid UTF-8 boundary",
            ),
            Self::InvalidOptions => write!(f, "invalid page or paragraph layout options"),
        }
    }
}

impl std::error::Error for LayoutError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn break_segments_keeps_all_text() {
        let text = "hello   world test";
        let ranges = break_segments(text);
        let reconstructed: String = ranges.iter().map(|range| &text[range.clone()]).collect();
        assert_eq!(reconstructed, text);
        assert_eq!(ranges.len(), 3);
    }

    #[test]
    fn explicit_break_opportunities_do_not_modify_text() {
        let text = "helloworld";
        let ranges = break_segments_with_opportunities(text, &[5]).unwrap();
        assert_eq!(ranges, vec![0..5, 5..10]);
        let reconstructed: String = ranges.iter().map(|range| &text[range.clone()]).collect();
        assert_eq!(reconstructed, text);
    }

    #[test]
    fn explicit_break_opportunities_must_be_utf8_boundaries() {
        let text = "ខ្មែរ";
        let error = break_segments_with_opportunities(text, &[1]).unwrap_err();
        assert_eq!(error, LayoutError::InvalidBreakOpportunity(1));
    }

    #[test]
    fn default_layout_has_positive_content_width() {
        assert!(LayoutOptions::default().content_width() > 0.0);
    }

    #[test]
    fn logical_paragraph_metadata_distinguishes_hard_and_soft_breaks() {
        let text = "abc\ndef";
        let first = LogicalParagraph {
            paragraph_index: 0,
            source_range: SourceRange::new(0, 3).unwrap(),
            unicode: text[..3].to_owned(),
            terminated_by_newline: true,
        };
        let second = LogicalParagraph {
            paragraph_index: 1,
            source_range: SourceRange::new(4, 7).unwrap(),
            unicode: text[4..].to_owned(),
            terminated_by_newline: false,
        };
        assert_eq!(first.unicode, "abc");
        assert!(first.terminated_by_newline);
        assert_eq!(second.unicode, "def");
        assert!(!second.terminated_by_newline);
    }

    #[test]
    fn layout_document_logical_text_keeps_only_hard_newlines() {
        let document = LayoutDocument {
            page_count: 2,
            runs: Vec::new(),
            paragraphs: vec![
                LogicalParagraph {
                    paragraph_index: 0,
                    source_range: SourceRange::new(0, 3).unwrap(),
                    unicode: "abc".to_owned(),
                    terminated_by_newline: true,
                },
                LogicalParagraph {
                    paragraph_index: 1,
                    source_range: SourceRange::new(4, 7).unwrap(),
                    unicode: "def".to_owned(),
                    terminated_by_newline: false,
                },
            ],
            line_count: 5,
        };
        assert_eq!(document.logical_text(), "abc\ndef");
    }
}

/// Axis-aligned rectangle in PDF user-space points.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PdfRect {
    /// Left edge.
    pub x0: f64,
    /// Bottom edge.
    pub y0: f64,
    /// Right edge.
    pub x1: f64,
    /// Top edge.
    pub y1: f64,
}

impl PdfRect {
    /// Returns this rectangle's width.
    #[must_use]
    pub fn width(self) -> f64 {
        (self.x1 - self.x0).max(0.0)
    }

    /// Returns this rectangle's height.
    #[must_use]
    pub fn height(self) -> f64 {
        (self.y1 - self.y0).max(0.0)
    }

    /// Returns the union of two rectangles.
    #[must_use]
    pub fn union(self, other: Self) -> Self {
        Self {
            x0: self.x0.min(other.x0),
            y0: self.y0.min(other.y0),
            x1: self.x1.max(other.x1),
            y1: self.y1.max(other.y1),
        }
    }
}

/// Expected selection geometry for one logical PDF unit.
#[derive(Clone, Debug, PartialEq)]
pub struct UnitGeometry {
    /// Zero-based physical page index.
    pub page_index: u32,
    /// Logical paragraph index.
    pub paragraph_index: u32,
    /// Original UTF-8 byte range, when available.
    pub source_range: Option<SourceRange>,
    /// Exact logical Unicode represented by the unit.
    pub unicode: String,
    /// Bounding rectangle in PDF user space.
    pub rect: PdfRect,
    /// Resolved run direction.
    pub direction: TextDirection,
    /// Font identity selected by fallback.
    pub font_id: FontId,
}

/// One visual line reconstructed from laid-out unit geometry.
#[derive(Clone, Debug, PartialEq)]
pub struct LineGeometry {
    /// Zero-based physical page index.
    pub page_index: u32,
    /// Baseline coordinate in PDF points.
    pub baseline_y: f64,
    /// Bounding rectangle of all units on the line.
    pub rect: PdfRect,
    /// Smallest source byte covered by the line.
    pub source_start: usize,
    /// Largest exclusive source byte covered by the line.
    pub source_end: usize,
}

/// Geometry index used to validate selection/highlight behavior.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct GeometryIndex {
    /// Geometry for every source-mapped logical unit.
    pub units: Vec<UnitGeometry>,
    /// Visual line bounds grouped by page and baseline.
    pub lines: Vec<LineGeometry>,
}

impl GeometryIndex {
    /// Builds deterministic expected selection geometry from a laid-out document.
    #[must_use]
    pub fn from_layout(document: &LayoutDocument) -> Self {
        let mut units = Vec::new();
        for run in &document.runs {
            let scale = run.font_size / f64::from(run.units_per_em);
            for unit in &run.units {
                let Some(source_range) = unit.source_range.clone() else {
                    continue;
                };
                let (min_x, max_x) = unit_visual_bounds_x(unit);
                let mut x0 = run.run_origin_x + f64::from(min_x) * scale;
                let mut x1 = run.run_origin_x + f64::from(max_x) * scale;
                if x1 < x0 {
                    std::mem::swap(&mut x0, &mut x1);
                }
                // Some combining-only or synthetic semantic units can have a
                // zero advance. Preserve a small selectable hit area without
                // changing the actual PDF text metrics.
                if (x1 - x0).abs() < 0.01 {
                    x1 = x0 + run.font_size * 0.2;
                }
                let rect = PdfRect {
                    x0,
                    y0: run.baseline_y - run.font_size * 0.25,
                    x1,
                    y1: run.baseline_y + run.font_size * 0.85,
                };
                units.push(UnitGeometry {
                    page_index: run.page_index,
                    paragraph_index: run.paragraph_index,
                    source_range: Some(source_range),
                    unicode: unit.unicode.clone(),
                    rect,
                    direction: run.direction,
                    font_id: unit.font_id,
                });
            }
        }
        units.sort_by_key(|unit| {
            unit.source_range
                .as_ref()
                .map_or(usize::MAX, SourceRange::start)
        });

        let mut line_seeds: Vec<(u32, f64, PdfRect, usize, usize)> = Vec::new();
        for run in &document.runs {
            let run_ranges: Vec<&SourceRange> = run
                .units
                .iter()
                .filter_map(|unit| unit.source_range.as_ref())
                .collect();
            let (Some(start), Some(end)) = (
                run_ranges.iter().map(|range| range.start()).min(),
                run_ranges.iter().map(|range| range.end()).max(),
            ) else {
                continue;
            };
            let run_rect = units
                .iter()
                .filter(|unit| {
                    unit.page_index == run.page_index
                        && unit
                            .source_range
                            .as_ref()
                            .is_some_and(|range| range.start() >= start && range.end() <= end)
                })
                .map(|unit| unit.rect)
                .reduce(PdfRect::union);
            let Some(run_rect) = run_rect else {
                continue;
            };
            if let Some(existing) = line_seeds.iter_mut().find(|(page, baseline, _, _, _)| {
                *page == run.page_index && (*baseline - run.baseline_y).abs() < 0.01
            }) {
                existing.2 = existing.2.union(run_rect);
                existing.3 = existing.3.min(start);
                existing.4 = existing.4.max(end);
            } else {
                line_seeds.push((run.page_index, run.baseline_y, run_rect, start, end));
            }
        }
        line_seeds.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal))
        });
        let lines = line_seeds
            .into_iter()
            .map(
                |(page_index, baseline_y, rect, source_start, source_end)| LineGeometry {
                    page_index,
                    baseline_y,
                    rect,
                    source_start,
                    source_end,
                },
            )
            .collect();
        Self { units, lines }
    }

    /// Returns visual rectangles intersecting an original UTF-8 source range.
    ///
    /// Rectangles are coalesced by physical line so cross-run/font selections
    /// yield one highlight rectangle per visual line whenever possible.
    #[must_use]
    pub fn selection_rects(&self, source: Range<usize>) -> Vec<(u32, PdfRect)> {
        let mut selected: Vec<&UnitGeometry> = self
            .units
            .iter()
            .filter(|unit| {
                unit.source_range
                    .as_ref()
                    .is_some_and(|range| range.start() < source.end && range.end() > source.start)
            })
            .collect();
        selected.sort_by_key(|unit| {
            (
                unit.page_index,
                unit.source_range
                    .as_ref()
                    .map_or(usize::MAX, SourceRange::start),
            )
        });

        let mut output: Vec<(u32, PdfRect)> = Vec::new();
        for unit in selected {
            let line = self.lines.iter().find(|line| {
                line.page_index == unit.page_index
                    && unit.rect.y0 <= line.rect.y1
                    && unit.rect.y1 >= line.rect.y0
            });
            let key_rect = line.map_or(unit.rect, |line| PdfRect {
                x0: unit.rect.x0,
                y0: line.rect.y0,
                x1: unit.rect.x1,
                y1: line.rect.y1,
            });
            if let Some((last_page, last_rect)) = output.last_mut() {
                if *last_page == unit.page_index
                    && (last_rect.y0 - key_rect.y0).abs() < 0.01
                    && (last_rect.y1 - key_rect.y1).abs() < 0.01
                {
                    *last_rect = last_rect.union(key_rect);
                    continue;
                }
            }
            output.push((unit.page_index, key_rect));
        }
        output
    }
}

fn unit_visual_bounds_x(unit: &LogicalPdfUnit) -> (i32, i32) {
    let mut points = unit
        .glyphs
        .iter()
        .flat_map(|glyph| [glyph.run_x, glyph.run_x.saturating_add(glyph.x_advance)]);
    let Some(first) = points.next() else {
        return (0, 0);
    };
    let mut min_x = first;
    let mut max_x = first;
    for point in points {
        min_x = min_x.min(point);
        max_x = max_x.max(point);
    }
    (min_x, max_x)
}

#[cfg(test)]
mod geometry_tests {
    use super::*;
    use unicode_pdf_core::PositionedGlyph;

    fn unit(text: &str, start: usize, end: usize, x: i32, advance: i32) -> LogicalPdfUnit {
        LogicalPdfUnit {
            unicode: text.to_owned(),
            source_range: Some(SourceRange::new(start, end).expect("range")),
            font_id: FontId(1),
            glyphs: vec![PositionedGlyph {
                glyph_id: 1,
                run_x: x,
                run_y: 0,
                x_offset: 0,
                y_offset: 0,
                x_advance: advance,
                y_advance: 0,
            }],
        }
    }

    #[test]
    fn geometry_preserves_source_order_and_coalesces_line_selection() {
        let document = LayoutDocument {
            page_count: 1,
            line_count: 1,
            paragraphs: Vec::new(),
            runs: vec![LayoutRun {
                font_index: 0,
                units_per_em: 1000,
                units: vec![unit("ក", 0, 3, 0, 500), unit("ខ", 3, 6, 500, 500)],
                direction: TextDirection::LeftToRight,
                language: "km",
                paragraph_index: 0,
                page_index: 0,
                run_origin_x: 50.0,
                baseline_y: 700.0,
                font_size: 20.0,
            }],
        };
        let geometry = GeometryIndex::from_layout(&document);
        assert_eq!(geometry.units.len(), 2);
        assert_eq!(geometry.lines.len(), 1);
        let rects = geometry.selection_rects(0..6);
        assert_eq!(rects.len(), 1);
        assert!((rects[0].1.width() - 20.0).abs() < 0.01);
    }

    #[test]
    fn geometry_keeps_cross_page_selection_separate() {
        let mut first = LayoutRun {
            font_index: 0,
            units_per_em: 1000,
            units: vec![unit("A", 0, 1, 0, 500)],
            direction: TextDirection::LeftToRight,
            language: "en",
            paragraph_index: 0,
            page_index: 0,
            run_origin_x: 50.0,
            baseline_y: 60.0,
            font_size: 20.0,
        };
        let mut second = first.clone();
        second.page_index = 1;
        second.baseline_y = 760.0;
        second.units = vec![unit("B", 1, 2, 0, 500)];
        first.units = vec![unit("A", 0, 1, 0, 500)];
        let geometry = GeometryIndex::from_layout(&LayoutDocument {
            page_count: 2,
            line_count: 2,
            paragraphs: Vec::new(),
            runs: vec![first, second],
        });
        let rects = geometry.selection_rects(0..2);
        assert_eq!(rects.len(), 2);
        assert_eq!(rects[0].0, 0);
        assert_eq!(rects[1].0, 1);
    }
}
