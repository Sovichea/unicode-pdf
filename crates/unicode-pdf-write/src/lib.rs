//! PDF text planning, authoritative `/ToUnicode`, and Type0/CIDFontType2 emission.
//!
//! The writer deliberately separates semantic order from visual placement. PDF
//! text operators are emitted in logical source order, while every logical CID
//! is positioned at the absolute visual coordinate produced by shaping.

use std::fmt;
use std::fmt::Write as _;

use unicode_pdf_core::{LogicalPdfUnit, TextDirection};
use unicode_pdf_font::{Cid, CidAllocator, FontError, SynthesizedTrueTypeFont};

/// Mapping from one PDF CID to exact logical Unicode.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToUnicodeEntry {
    /// PDF character identifier.
    pub cid: Cid,
    /// Exact logical Unicode represented by this CID.
    pub unicode: String,
}

/// One unit in a planned PDF text run.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PlannedUnit {
    /// Allocated PDF CID.
    pub cid: Cid,
    /// Exact logical Unicode.
    pub unicode: String,
    /// Minimum visual X of the synthetic glyph in shaping font units.
    pub visual_x: i32,
    /// Maximum visual X of the synthetic glyph in shaping font units.
    pub visual_end_x: i32,
}

/// Result of assigning CIDs to a logical run.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TextPlan {
    /// Units in semantic logical order.
    pub units: Vec<PlannedUnit>,
    /// Unique CID to Unicode mappings known when this plan was built.
    pub to_unicode: Vec<ToUnicodeEntry>,
}

impl TextPlan {
    /// Returns the logical Unicode represented by the plan.
    #[must_use]
    pub fn extracted_text(&self) -> String {
        self.units
            .iter()
            .map(|unit| unit.unicode.as_str())
            .collect()
    }

    /// Returns the CID text sequence as hexadecimal bytes suitable for an
    /// Identity-H text string.
    #[must_use]
    pub fn cid_hex_string(&self) -> String {
        let mut output = String::with_capacity(self.units.len() * 4);
        for unit in &self.units {
            let _ = write!(output, "{:04X}", unit.cid.0);
        }
        output
    }

    /// Returns the minimum visual X origin used by this run.
    #[must_use]
    pub fn min_visual_x(&self) -> i32 {
        self.units
            .iter()
            .map(|unit| unit.visual_x)
            .min()
            .unwrap_or(0)
    }

    /// Returns the maximum visual X edge used by this run.
    #[must_use]
    pub fn max_visual_x(&self) -> i32 {
        self.units
            .iter()
            .map(|unit| unit.visual_end_x)
            .max()
            .unwrap_or(0)
    }

    /// Returns the visual width of the shaped run in font units.
    #[must_use]
    pub fn visual_width(&self) -> i32 {
        self.max_visual_x().saturating_sub(self.min_visual_x())
    }
}

/// Allocates/reuses CIDs and creates an authoritative logical text plan.
///
/// # Errors
///
/// Returns [`FontError::CidSpaceExhausted`] if the current CID subset is full.
pub fn plan_text_run(
    units: &[LogicalPdfUnit],
    allocator: &mut CidAllocator,
) -> Result<TextPlan, FontError> {
    let mut planned = Vec::with_capacity(units.len());

    for unit in units {
        let cid = allocator.get_or_allocate(unit)?;
        let (visual_x, visual_end_x) = visual_bounds_x(unit);
        planned.push(PlannedUnit {
            cid,
            unicode: unit.unicode.clone(),
            visual_x,
            visual_end_x,
        });
    }

    let to_unicode = allocator
        .entries()
        .iter()
        .map(|entry| ToUnicodeEntry {
            cid: entry.cid,
            unicode: entry.key.unicode.clone(),
        })
        .collect();

    Ok(TextPlan {
        units: planned,
        to_unicode,
    })
}

fn visual_bounds_x(unit: &LogicalPdfUnit) -> (i32, i32) {
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

/// Builds a complete `/ToUnicode` `CMap` for 16-bit CIDs.
#[must_use]
pub fn build_to_unicode_cmap(entries: &[ToUnicodeEntry]) -> String {
    let mut output = String::new();
    output.push_str("/CIDInit /ProcSet findresource begin\n");
    output.push_str("12 dict begin\n");
    output.push_str("begincmap\n");
    output.push_str("/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n");
    output.push_str("/CMapName /UnicodePdfLogicalText def\n");
    output.push_str("/CMapType 2 def\n");
    output.push_str("1 begincodespacerange\n");
    output.push_str("<0000> <FFFF>\n");
    output.push_str("endcodespacerange\n");

    for chunk in entries.chunks(100) {
        let _ = writeln!(output, "{} beginbfchar", chunk.len());
        for entry in chunk {
            let _ = writeln!(
                output,
                "<{:04X}> <{}>",
                entry.cid.0,
                utf16be_hex(&entry.unicode)
            );
        }
        output.push_str("endbfchar\n");
    }

    output.push_str("endcmap\n");
    output.push_str("CMapName currentdict /CMap defineresource pop\n");
    output.push_str("end\n");
    output.push_str("end\n");
    output
}

/// Encodes a Unicode string as uppercase UTF-16BE hexadecimal without a BOM.
#[must_use]
pub fn utf16be_hex(text: &str) -> String {
    let mut output = String::new();
    for code_unit in text.encode_utf16() {
        let _ = write!(output, "{code_unit:04X}");
    }
    output
}

/// Placement and semantic metadata for one shaped logical run on a PDF page.
#[derive(Clone, Copy, Debug)]
pub struct PlacedTextRun<'a> {
    /// Logical CID plan for the run.
    pub plan: &'a TextPlan,
    /// PDF X coordinate, in points, corresponding to shaping run X = 0.
    pub run_origin_x: f64,
    /// Baseline Y coordinate in PDF points.
    pub baseline_y: f64,
    /// Resolved text direction for this semantic run.
    pub direction: TextDirection,
    /// Optional BCP 47 language tag for this run.
    pub language: Option<&'a str>,
    /// Logical paragraph identifier. Runs with the same identifier are grouped
    /// under one `/P` structure element.
    pub paragraph_index: u32,
}

/// Controls where replacement text is emitted in addition to `/ToUnicode`.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ActualTextPolicy {
    /// Do not emit `/ActualText`. `/ToUnicode` remains authoritative.
    #[default]
    Off,
    /// Add `/ActualText` only around multi-codepoint logical units.
    ComplexUnits,
    /// Add `/ActualText` around every right-to-left semantic run.
    RtlRuns,
    /// Add `/ActualText` around every semantic run.
    AllRuns,
}

/// Controls paragraph-level replacement text independently from run-level
/// [`ActualTextPolicy`].
///
/// Paragraph replacement text is sourced from the compiler's pre-layout
/// logical paragraph, never reconstructed from visual line geometry.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum ParagraphTextPolicy {
    /// Do not add paragraph-level `/ActualText`.
    #[default]
    Off,
    /// Put exact paragraph Unicode on the `/P` structure element.
    StructureActualText,
    /// Wrap each page-local paragraph fragment in marked-content `/ActualText`.
    PageFragmentActualText,
    /// Emit both structure-level and page-fragment replacement text.
    StructureAndPageFragment,
}

impl ParagraphTextPolicy {
    fn structure_actual_text(self) -> bool {
        matches!(
            self,
            Self::StructureActualText | Self::StructureAndPageFragment
        )
    }

    fn page_fragment_actual_text(self) -> bool {
        matches!(
            self,
            Self::PageFragmentActualText | Self::StructureAndPageFragment
        )
    }
}

/// Authoritative semantic text for one logical paragraph.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DocumentParagraphText {
    /// Paragraph identifier shared with [`DocumentPlacedTextRun::paragraph_index`].
    pub paragraph_index: u32,
    /// Exact logical Unicode before visual wrapping and pagination.
    pub unicode: String,
    /// Whether a hard source newline followed the paragraph.
    pub terminated_by_newline: bool,
}

/// One embedded Type0 font resource in a multi-font PDF document.
#[derive(Clone, Debug)]
pub struct EmbeddedType0Font<'a> {
    /// Synthesized TrueType font containing the CIDs used by this resource.
    pub font: &'a SynthesizedTrueTypeFont,
    /// Source font units-per-em used by shaping.
    pub units_per_em: u32,
    /// PDF-safe PostScript font name without a leading slash.
    pub base_font_name: String,
}

/// One semantic text run placed in a multi-font, multi-page document.
#[derive(Clone, Copy, Debug)]
pub struct DocumentPlacedTextRun<'a> {
    /// Logical CID plan for the run.
    pub plan: &'a TextPlan,
    /// Index in the embedded font resource array.
    pub font_index: usize,
    /// Zero-based physical page index.
    pub page_index: u32,
    /// PDF X coordinate corresponding to shaping run X = 0.
    pub run_origin_x: f64,
    /// Baseline Y coordinate in PDF points.
    pub baseline_y: f64,
    /// Font size in PDF points.
    pub font_size: f64,
    /// Resolved text direction for this semantic run.
    pub direction: TextDirection,
    /// Optional BCP 47 language tag for this run.
    pub language: Option<&'a str>,
    /// Logical paragraph identifier.
    pub paragraph_index: u32,
}

/// Options for multi-font, multi-page Type0 PDF emission.
#[derive(Clone, Debug)]
pub struct Type0DocumentOptions {
    /// Page width in PDF points.
    pub page_width: f64,
    /// Page height in PDF points.
    pub page_height: f64,
    /// Emit a tagged-PDF structure tree and page-local MCIDs.
    pub tagged: bool,
    /// Document-level BCP 47 language tag.
    pub document_language: String,
    /// Scope for optional `/ActualText` replacement text.
    pub actual_text: ActualTextPolicy,
    /// Exact compiler-owned paragraph text used for semantic replacement.
    pub paragraphs: Vec<DocumentParagraphText>,
    /// Controls paragraph-level semantic replacement text.
    pub paragraph_text: ParagraphTextPolicy,
}

impl Default for Type0DocumentOptions {
    fn default() -> Self {
        Self {
            page_width: 595.0,
            page_height: 842.0,
            tagged: true,
            document_language: "und".to_owned(),
            actual_text: ActualTextPolicy::Off,
            paragraphs: Vec::new(),
            paragraph_text: ParagraphTextPolicy::Off,
        }
    }
}

/// Options for one-page Type0 PDF emission.
#[derive(Clone, Debug)]
pub struct Type0PdfOptions {
    /// Page width in PDF points.
    pub page_width: f64,
    /// Page height in PDF points.
    pub page_height: f64,
    /// Text size in PDF points.
    pub font_size: f64,
    /// PDF-safe PostScript font name without a leading slash.
    pub base_font_name: String,
    /// Emit a tagged-PDF structure tree and MCIDs for semantic runs.
    pub tagged: bool,
    /// Document-level BCP 47 language tag. Use `und` when unknown.
    pub document_language: String,
    /// Scope for optional `/ActualText` replacement text.
    pub actual_text: ActualTextPolicy,
}

impl Default for Type0PdfOptions {
    fn default() -> Self {
        Self {
            page_width: 595.0,
            page_height: 842.0,
            font_size: 24.0,
            base_font_name: "UPDFAB+UnicodePdfSynthetic".to_owned(),
            tagged: true,
            document_language: "und".to_owned(),
            actual_text: ActualTextPolicy::Off,
        }
    }
}

/// Errors produced while serializing a Type0/CIDFontType2 PDF.
#[derive(Clone, Debug, PartialEq)]
pub enum PdfWriteError {
    /// Units-per-em must be nonzero.
    InvalidUnitsPerEm,
    /// A page/font metric is not finite or is outside the supported range.
    InvalidMetric(&'static str),
    /// The synthesized TrueType font is structurally invalid for embedding.
    InvalidTrueType(&'static str),
    /// A planned CID has no synthetic TrueType glyph.
    MissingCidGlyph(Cid),
    /// PDF object or stream size exceeds 32-bit implementation limits.
    DocumentTooLarge,
}

impl fmt::Display for PdfWriteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidUnitsPerEm => write!(f, "units-per-em must be nonzero"),
            Self::InvalidMetric(name) => write!(f, "invalid PDF metric: {name}"),
            Self::InvalidTrueType(message) => {
                write!(f, "invalid embedded TrueType font: {message}")
            }
            Self::MissingCidGlyph(cid) => write!(f, "CID {} has no synthesized glyph", cid.0),
            Self::DocumentTooLarge => write!(f, "PDF exceeds supported object/stream size"),
        }
    }
}

impl std::error::Error for PdfWriteError {}

/// Emits one standards-oriented PDF page using an embedded Type0 font with a
/// `CIDFontType2` descendant, explicit `/CIDToGIDMap`, authoritative
/// `/ToUnicode`, and logical-order text operators at absolute visual positions.
///
/// The synthesized font must have been built from the same [`CidAllocator`]
/// entries used to create the supplied [`TextPlan`] values.
///
/// # Errors
///
/// Returns [`PdfWriteError`] when metrics are invalid, the synthesized font is
/// malformed, or a plan references a CID absent from the font.
pub fn build_type0_single_page_pdf(
    font: &SynthesizedTrueTypeFont,
    units_per_em: u32,
    runs: &[PlacedTextRun<'_>],
    options: &Type0PdfOptions,
) -> Result<Vec<u8>, PdfWriteError> {
    validate_options(units_per_em, options)?;
    let metrics = TrueTypePdfMetrics::parse(&font.bytes, units_per_em)?;
    let resources = build_cid_resources(font, units_per_em, runs)?;
    let marked_runs = build_marked_runs(runs)?;
    let content = build_content_stream(runs, units_per_em, options, &marked_runs)?;
    let base_font = pdf_name(&options.base_font_name);

    let mut pdf = PdfBuilder::new();
    let catalog = pdf.reserve();
    let pages = pdf.reserve();
    let page = pdf.reserve();
    let content_obj = pdf.add_stream(&[], &content)?;
    let type0_font = embed_type0_font(&mut pdf, font, &resources, metrics, &base_font)?;

    let tagging = if options.tagged {
        Some(embed_structure_tree(
            &mut pdf,
            page,
            &marked_runs,
            &options.document_language,
        )?)
    } else {
        None
    };

    let tag_page_entries = tagging
        .as_ref()
        .map_or_else(String::new, |_| " /StructParents 0 /Tabs /S".to_owned());
    pdf.set(
        page,
        format!(
            "<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {} {}] \
             /Resources << /Font << /F0 {type0_font} 0 R >> >> /Contents {content_obj} 0 R{tag_page_entries} >>",
            pdf_number(options.page_width),
            pdf_number(options.page_height)
        )
        .into_bytes(),
    );
    pdf.set(
        pages,
        format!("<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>").into_bytes(),
    );

    let catalog_tag_entries = if let Some(tagging) = &tagging {
        format!(
            " /StructTreeRoot {} 0 R /MarkInfo << /Marked true >> /Lang {}",
            tagging.struct_tree_root,
            pdf_text_string(&options.document_language)
        )
    } else {
        String::new()
    };
    pdf.set(
        catalog,
        format!("<< /Type /Catalog /Pages {pages} 0 R{catalog_tag_entries} >>").into_bytes(),
    );

    pdf.finish(catalog)
}

/// Emits a multi-font, multi-page tagged PDF using embedded Type0/CIDFontType2
/// resources. Logical operator order remains independent of visual placement.
///
/// # Errors
///
/// Returns [`PdfWriteError`] for invalid metrics, missing CIDs, out-of-range
/// page/font references, malformed synthesized fonts, or oversized documents.
#[allow(clippy::too_many_lines)]
pub fn build_type0_document_pdf(
    fonts: &[EmbeddedType0Font<'_>],
    page_count: u32,
    runs: &[DocumentPlacedTextRun<'_>],
    options: &Type0DocumentOptions,
) -> Result<Vec<u8>, PdfWriteError> {
    if fonts.is_empty() {
        return Err(PdfWriteError::InvalidMetric("font resource count"));
    }
    if page_count == 0 {
        return Err(PdfWriteError::InvalidMetric("page count"));
    }
    validate_document_options(options)?;
    for font in fonts {
        if font.units_per_em == 0 {
            return Err(PdfWriteError::InvalidUnitsPerEm);
        }
    }
    for run in runs {
        if run.font_index >= fonts.len() {
            return Err(PdfWriteError::InvalidMetric("run font index"));
        }
        if run.page_index >= page_count {
            return Err(PdfWriteError::InvalidMetric("run page index"));
        }
        if !run.run_origin_x.is_finite()
            || !run.baseline_y.is_finite()
            || !run.font_size.is_finite()
            || run.font_size <= 0.0
        {
            return Err(PdfWriteError::InvalidMetric("run placement"));
        }
        let font = fonts[run.font_index].font;
        for unit in &run.plan.units {
            if !font
                .synthetic_glyphs
                .iter()
                .any(|record| record.cid == unit.cid)
            {
                return Err(PdfWriteError::MissingCidGlyph(unit.cid));
            }
        }
    }

    let marked_runs = build_document_marked_runs(runs, page_count)?;
    let mut pdf = PdfBuilder::new();
    let catalog = pdf.reserve();
    let pages = pdf.reserve();
    let page_refs: Vec<usize> = (0..page_count).map(|_| pdf.reserve()).collect();

    let mut font_refs = Vec::with_capacity(fonts.len());
    for font in fonts {
        let metrics = TrueTypePdfMetrics::parse(&font.font.bytes, font.units_per_em)?;
        let resources = build_cid_resources_for_font(font.font, font.units_per_em)?;
        let base_font = pdf_name(&font.base_font_name);
        font_refs.push(embed_type0_font(
            &mut pdf, font.font, &resources, metrics, &base_font,
        )?);
    }

    let mut content_refs = Vec::with_capacity(page_refs.len());
    for page_index in 0..page_count {
        let content = build_document_page_content(page_index, runs, fonts, options, &marked_runs)?;
        content_refs.push(pdf.add_stream(&[], &content)?);
    }

    let tagging = if options.tagged {
        Some(embed_document_structure_tree(
            &mut pdf,
            &page_refs,
            &marked_runs,
            &options.document_language,
            &options.paragraphs,
            options.paragraph_text,
        )?)
    } else {
        None
    };

    let font_dictionary = font_refs
        .iter()
        .enumerate()
        .map(|(index, reference)| format!("/F{index} {reference} 0 R"))
        .collect::<Vec<_>>()
        .join(" ");

    for (page_index, (page_ref, content_ref)) in page_refs.iter().zip(&content_refs).enumerate() {
        let tag_entries = if options.tagged {
            format!(" /StructParents {page_index} /Tabs /S")
        } else {
            String::new()
        };
        pdf.set(
            *page_ref,
            format!(
                "<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {} {}] \
                 /Resources << /Font << {font_dictionary} >> >> /Contents {content_ref} 0 R{tag_entries} >>",
                pdf_number(options.page_width),
                pdf_number(options.page_height)
            )
            .into_bytes(),
        );
    }

    let kids = page_refs
        .iter()
        .map(|reference| format!("{reference} 0 R"))
        .collect::<Vec<_>>()
        .join(" ");
    pdf.set(
        pages,
        format!("<< /Type /Pages /Kids [{kids}] /Count {page_count} >>").into_bytes(),
    );

    let catalog_tag_entries = if let Some(tagging) = &tagging {
        format!(
            " /StructTreeRoot {} 0 R /MarkInfo << /Marked true >> /Lang {}",
            tagging.struct_tree_root,
            pdf_text_string(&options.document_language)
        )
    } else {
        String::new()
    };
    pdf.set(
        catalog,
        format!("<< /Type /Catalog /Pages {pages} 0 R{catalog_tag_entries} >>").into_bytes(),
    );
    pdf.finish(catalog)
}

#[derive(Clone, Debug)]
struct DocumentMarkedRun {
    run_index: usize,
    page_index: u32,
    mcid: u32,
    paragraph_index: u32,
    direction: TextDirection,
    language: Option<String>,
}

fn build_document_marked_runs(
    runs: &[DocumentPlacedTextRun<'_>],
    page_count: u32,
) -> Result<Vec<DocumentMarkedRun>, PdfWriteError> {
    let page_count = usize::try_from(page_count).map_err(|_| PdfWriteError::DocumentTooLarge)?;
    let mut next_mcid = vec![0_u32; page_count];
    let mut marked = Vec::new();
    for (run_index, run) in runs.iter().enumerate() {
        if run.plan.units.is_empty() {
            continue;
        }
        let page = usize::try_from(run.page_index).map_err(|_| PdfWriteError::DocumentTooLarge)?;
        let mcid = next_mcid[page];
        next_mcid[page] = mcid.checked_add(1).ok_or(PdfWriteError::DocumentTooLarge)?;
        marked.push(DocumentMarkedRun {
            run_index,
            page_index: run.page_index,
            mcid,
            paragraph_index: run.paragraph_index,
            direction: run.direction,
            language: run.language.map(str::to_owned),
        });
    }
    Ok(marked)
}

#[allow(clippy::too_many_lines)]
fn embed_document_structure_tree(
    pdf: &mut PdfBuilder,
    page_refs: &[usize],
    marked_runs: &[DocumentMarkedRun],
    document_language: &str,
    paragraphs: &[DocumentParagraphText],
    paragraph_text_policy: ParagraphTextPolicy,
) -> Result<TaggingObjects, PdfWriteError> {
    let struct_tree_root = pdf.reserve();
    let parent_tree = pdf.reserve();
    let document_ref = pdf.reserve();

    let mut paragraph_ids = Vec::<u32>::new();
    for run in marked_runs {
        if !paragraph_ids.contains(&run.paragraph_index) {
            paragraph_ids.push(run.paragraph_index);
        }
    }
    let paragraph_refs: Vec<usize> = paragraph_ids.iter().map(|_| pdf.reserve()).collect();
    let span_refs: Vec<usize> = marked_runs.iter().map(|_| pdf.reserve()).collect();

    for (run, span_ref) in marked_runs.iter().zip(&span_refs) {
        let paragraph_position = paragraph_ids
            .iter()
            .position(|id| *id == run.paragraph_index)
            .ok_or(PdfWriteError::DocumentTooLarge)?;
        let paragraph_ref = paragraph_refs[paragraph_position];
        let page_ref = *page_refs
            .get(usize::try_from(run.page_index).map_err(|_| PdfWriteError::DocumentTooLarge)?)
            .ok_or(PdfWriteError::DocumentTooLarge)?;
        let lang = run.language.as_deref().unwrap_or(document_language);
        let writing_mode = match run.direction {
            TextDirection::RightToLeft => "/RlTb",
            TextDirection::LeftToRight | TextDirection::Auto => "/LrTb",
        };
        pdf.set(
            *span_ref,
            format!(
                "<< /Type /StructElem /S /Span /P {paragraph_ref} 0 R /Pg {page_ref} 0 R \
                 /K {} /Lang {} /A << /O /Layout /WritingMode {writing_mode} >> >>",
                run.mcid,
                pdf_text_string(lang)
            )
            .into_bytes(),
        );
    }

    for (paragraph_id, paragraph_ref) in paragraph_ids.iter().zip(&paragraph_refs) {
        let kids = marked_runs
            .iter()
            .zip(&span_refs)
            .filter(|(run, _)| run.paragraph_index == *paragraph_id)
            .map(|(_, span_ref)| format!("{span_ref} 0 R"))
            .collect::<Vec<_>>()
            .join(" ");
        let actual_text = if paragraph_text_policy.structure_actual_text() {
            paragraphs
                .iter()
                .find(|paragraph| paragraph.paragraph_index == *paragraph_id)
                .map(|paragraph| format!(" /ActualText {}", pdf_text_string(&paragraph.unicode)))
                .unwrap_or_default()
        } else {
            String::new()
        };
        pdf.set(
            *paragraph_ref,
            format!("<< /Type /StructElem /S /P /P {document_ref} 0 R /K [{kids}]{actual_text} >>")
                .into_bytes(),
        );
    }

    let mut parent_nums = String::new();
    for page_index in 0..page_refs.len() {
        let mut page_spans: Vec<(&DocumentMarkedRun, usize)> = marked_runs
            .iter()
            .zip(&span_refs)
            .filter(|(run, _)| usize::try_from(run.page_index).ok() == Some(page_index))
            .map(|(run, reference)| (run, *reference))
            .collect();
        page_spans.sort_by_key(|(run, _)| run.mcid);
        if !parent_nums.is_empty() {
            parent_nums.push(' ');
        }
        let refs = page_spans
            .iter()
            .map(|(_, reference)| format!("{reference} 0 R"))
            .collect::<Vec<_>>()
            .join(" ");
        let _ = write!(parent_nums, "{page_index} [{refs}]");
    }
    pdf.set(
        parent_tree,
        format!("<< /Nums [{parent_nums}] >>").into_bytes(),
    );

    let document_kids = paragraph_refs
        .iter()
        .map(|reference| format!("{reference} 0 R"))
        .collect::<Vec<_>>()
        .join(" ");
    pdf.set(
        document_ref,
        format!(
            "<< /Type /StructElem /S /Document /P {struct_tree_root} 0 R /K [{document_kids}] >>"
        )
        .into_bytes(),
    );
    pdf.set(
        struct_tree_root,
        format!(
            "<< /Type /StructTreeRoot /K [{document_ref} 0 R] /ParentTree {parent_tree} 0 R \
             /ParentTreeNextKey {} >>",
            page_refs.len()
        )
        .into_bytes(),
    );
    Ok(TaggingObjects { struct_tree_root })
}

fn validate_document_options(options: &Type0DocumentOptions) -> Result<(), PdfWriteError> {
    for (name, value) in [
        ("page_width", options.page_width),
        ("page_height", options.page_height),
    ] {
        if !value.is_finite() || value <= 0.0 {
            return Err(PdfWriteError::InvalidMetric(name));
        }
    }
    for (index, paragraph) in options.paragraphs.iter().enumerate() {
        if options.paragraphs[..index]
            .iter()
            .any(|other| other.paragraph_index == paragraph.paragraph_index)
        {
            return Err(PdfWriteError::InvalidMetric("duplicate paragraph index"));
        }
    }
    Ok(())
}

fn page_paragraph_fragment_text(
    page_index: u32,
    paragraph_index: u32,
    runs: &[DocumentPlacedTextRun<'_>],
) -> String {
    runs.iter()
        .filter(|run| {
            run.page_index == page_index
                && run.paragraph_index == paragraph_index
                && !run.plan.units.is_empty()
        })
        .map(|run| run.plan.extracted_text())
        .collect()
}

fn is_first_page_paragraph_run(
    run_index: usize,
    run: &DocumentPlacedTextRun<'_>,
    runs: &[DocumentPlacedTextRun<'_>],
) -> bool {
    !runs[..run_index].iter().any(|other| {
        other.page_index == run.page_index
            && other.paragraph_index == run.paragraph_index
            && !other.plan.units.is_empty()
    })
}

fn is_last_page_paragraph_run(
    run_index: usize,
    run: &DocumentPlacedTextRun<'_>,
    runs: &[DocumentPlacedTextRun<'_>],
) -> bool {
    !runs[run_index + 1..].iter().any(|other| {
        other.page_index == run.page_index
            && other.paragraph_index == run.paragraph_index
            && !other.plan.units.is_empty()
    })
}

fn build_document_page_content(
    page_index: u32,
    runs: &[DocumentPlacedTextRun<'_>],
    fonts: &[EmbeddedType0Font<'_>],
    options: &Type0DocumentOptions,
    marked_runs: &[DocumentMarkedRun],
) -> Result<Vec<u8>, PdfWriteError> {
    let mut content = String::from("BT\n");
    for (run_index, run) in runs.iter().enumerate() {
        if run.page_index != page_index || run.plan.units.is_empty() {
            continue;
        }
        let font = &fonts[run.font_index];
        let scale = run.font_size / f64::from(font.units_per_em);
        let _ = writeln!(
            content,
            "/F{} {} Tf",
            run.font_index,
            pdf_number(run.font_size)
        );

        let fragment_actual_text = options.paragraph_text.page_fragment_actual_text();
        let first_fragment_run =
            fragment_actual_text && is_first_page_paragraph_run(run_index, run, runs);
        let last_fragment_run =
            fragment_actual_text && is_last_page_paragraph_run(run_index, run, runs);
        if first_fragment_run {
            let replacement = page_paragraph_fragment_text(page_index, run.paragraph_index, runs);
            let _ = writeln!(
                content,
                "/Span << /ActualText {} >> BDC",
                pdf_text_string(&replacement)
            );
        }

        if options.tagged {
            let marked = marked_runs
                .iter()
                .find(|marked| marked.run_index == run_index)
                .ok_or(PdfWriteError::DocumentTooLarge)?;
            let _ = writeln!(content, "/Span << /MCID {} >> BDC", marked.mcid);
        }

        let run_actual_text = matches!(options.actual_text, ActualTextPolicy::AllRuns)
            || matches!(options.actual_text, ActualTextPolicy::RtlRuns)
                && run.direction == TextDirection::RightToLeft;

        let can_emit_contiguous = run.direction == TextDirection::LeftToRight
            && matches!(options.actual_text, ActualTextPolicy::Off);
        if can_emit_contiguous {
            let x = run.run_origin_x + f64::from(run.plan.min_visual_x()) * scale;
            let _ = writeln!(
                content,
                "1 0 0 1 {} {} Tm <{}> Tj",
                pdf_number(x),
                pdf_number(run.baseline_y),
                run.plan.cid_hex_string()
            );
            if options.tagged {
                content.push_str("EMC\n");
            }
            if last_fragment_run {
                content.push_str("EMC\n");
            }
            continue;
        }

        if run_actual_text {
            let _ = writeln!(
                content,
                "/Span << /ActualText {} >> BDC",
                pdf_text_string(&run.plan.extracted_text())
            );
        }

        for unit in &run.plan.units {
            let unit_actual_text = matches!(options.actual_text, ActualTextPolicy::ComplexUnits)
                && unit.unicode.chars().count() > 1;
            if unit_actual_text {
                let _ = writeln!(
                    content,
                    "/Span << /ActualText {} >> BDC",
                    pdf_text_string(&unit.unicode)
                );
            }
            let x = run.run_origin_x + f64::from(unit.visual_x) * scale;
            let _ = writeln!(
                content,
                "1 0 0 1 {} {} Tm <{:04X}> Tj",
                pdf_number(x),
                pdf_number(run.baseline_y),
                unit.cid.0
            );
            if unit_actual_text {
                content.push_str("EMC\n");
            }
        }
        if run_actual_text {
            content.push_str("EMC\n");
        }
        if options.tagged {
            content.push_str("EMC\n");
        }
        if last_fragment_run {
            content.push_str("EMC\n");
        }
    }
    content.push_str("ET\n");
    Ok(content.into_bytes())
}

fn build_cid_resources_for_font(
    font: &SynthesizedTrueTypeFont,
    units_per_em: u32,
) -> Result<CidPdfResources, PdfWriteError> {
    let max_cid = font
        .synthetic_glyphs
        .iter()
        .map(|record| record.cid.0)
        .max()
        .unwrap_or(0);
    let mut cid_to_gid = vec![0_u8; (usize::from(max_cid) + 1) * 2];
    let mut widths = vec![0_i32; usize::from(max_cid) + 1];
    let mut unicode_entries = Vec::with_capacity(font.synthetic_glyphs.len());
    for record in &font.synthetic_glyphs {
        let index = usize::from(record.cid.0);
        cid_to_gid[index * 2..index * 2 + 2].copy_from_slice(&record.glyph_id.to_be_bytes());
        widths[index] = scale_1000(i32::from(record.advance_width), units_per_em)?;
        unicode_entries.push(ToUnicodeEntry {
            cid: record.cid,
            unicode: record.unicode.clone(),
        });
    }
    unicode_entries.sort_by_key(|entry| entry.cid);
    Ok(CidPdfResources {
        cid_to_gid,
        widths,
        to_unicode: build_to_unicode_cmap(&unicode_entries).into_bytes(),
    })
}

#[derive(Clone, Debug)]
struct MarkedRun {
    run_index: usize,
    mcid: u32,
    paragraph_index: u32,
    direction: TextDirection,
    language: Option<String>,
}

fn build_marked_runs(runs: &[PlacedTextRun<'_>]) -> Result<Vec<MarkedRun>, PdfWriteError> {
    let mut marked = Vec::new();
    for (run_index, run) in runs.iter().enumerate() {
        if run.plan.units.is_empty() {
            continue;
        }
        let mcid = u32::try_from(marked.len()).map_err(|_| PdfWriteError::DocumentTooLarge)?;
        marked.push(MarkedRun {
            run_index,
            mcid,
            paragraph_index: run.paragraph_index,
            direction: run.direction,
            language: run.language.map(str::to_owned),
        });
    }
    Ok(marked)
}

#[derive(Clone, Copy, Debug)]
struct TaggingObjects {
    struct_tree_root: usize,
}

fn embed_structure_tree(
    pdf: &mut PdfBuilder,
    page: usize,
    marked_runs: &[MarkedRun],
    document_language: &str,
) -> Result<TaggingObjects, PdfWriteError> {
    let struct_tree_root = pdf.reserve();
    let parent_tree = pdf.reserve();
    let document_ref = pdf.reserve();

    let mut paragraph_ids = Vec::<u32>::new();
    for run in marked_runs {
        if !paragraph_ids.contains(&run.paragraph_index) {
            paragraph_ids.push(run.paragraph_index);
        }
    }
    let paragraph_refs: Vec<usize> = paragraph_ids.iter().map(|_| pdf.reserve()).collect();
    let span_refs: Vec<usize> = marked_runs.iter().map(|_| pdf.reserve()).collect();

    for (run, span_ref) in marked_runs.iter().zip(&span_refs) {
        let paragraph_position = paragraph_ids
            .iter()
            .position(|id| *id == run.paragraph_index)
            .ok_or(PdfWriteError::DocumentTooLarge)?;
        let paragraph_ref = paragraph_refs[paragraph_position];
        let lang = run.language.as_deref().unwrap_or(document_language);
        let writing_mode = match run.direction {
            TextDirection::RightToLeft => "/RlTb",
            TextDirection::LeftToRight | TextDirection::Auto => "/LrTb",
        };
        pdf.set(
            *span_ref,
            format!(
                "<< /Type /StructElem /S /Span /P {paragraph_ref} 0 R /Pg {page} 0 R \
                 /K {} /Lang {} /A << /O /Layout /WritingMode {writing_mode} >> >>",
                run.mcid,
                pdf_text_string(lang)
            )
            .into_bytes(),
        );
    }

    for (paragraph_id, paragraph_ref) in paragraph_ids.iter().zip(&paragraph_refs) {
        let kids = marked_runs
            .iter()
            .zip(&span_refs)
            .filter(|(run, _)| run.paragraph_index == *paragraph_id)
            .map(|(_, span_ref)| format!("{span_ref} 0 R"))
            .collect::<Vec<_>>()
            .join(" ");
        pdf.set(
            *paragraph_ref,
            format!("<< /Type /StructElem /S /P /P {document_ref} 0 R /K [{kids}] >>").into_bytes(),
        );
    }

    let parent_array = span_refs
        .iter()
        .map(|reference| format!("{reference} 0 R"))
        .collect::<Vec<_>>()
        .join(" ");
    pdf.set(
        parent_tree,
        format!("<< /Nums [0 [{parent_array}]] >>").into_bytes(),
    );
    let document_kids = paragraph_refs
        .iter()
        .map(|reference| format!("{reference} 0 R"))
        .collect::<Vec<_>>()
        .join(" ");
    pdf.set(
        document_ref,
        format!(
            "<< /Type /StructElem /S /Document /P {struct_tree_root} 0 R /K [{document_kids}] >>"
        )
        .into_bytes(),
    );
    pdf.set(
        struct_tree_root,
        format!(
            "<< /Type /StructTreeRoot /K [{document_ref} 0 R] /ParentTree {parent_tree} 0 R \
             /ParentTreeNextKey 1 >>"
        )
        .into_bytes(),
    );

    Ok(TaggingObjects { struct_tree_root })
}

fn pdf_text_string(text: &str) -> String {
    format!("<FEFF{}>", utf16be_hex(text))
}

#[derive(Debug)]
struct CidPdfResources {
    cid_to_gid: Vec<u8>,
    widths: Vec<i32>,
    to_unicode: Vec<u8>,
}

fn build_cid_resources(
    font: &SynthesizedTrueTypeFont,
    units_per_em: u32,
    runs: &[PlacedTextRun<'_>],
) -> Result<CidPdfResources, PdfWriteError> {
    let max_cid = font
        .synthetic_glyphs
        .iter()
        .map(|record| record.cid.0)
        .max()
        .unwrap_or(0);
    let mut cid_to_gid = vec![0_u8; (usize::from(max_cid) + 1) * 2];
    let mut widths = vec![0_i32; usize::from(max_cid) + 1];
    let mut unicode_entries = Vec::with_capacity(font.synthetic_glyphs.len());

    for record in &font.synthetic_glyphs {
        let index = usize::from(record.cid.0);
        cid_to_gid[index * 2..index * 2 + 2].copy_from_slice(&record.glyph_id.to_be_bytes());
        widths[index] = scale_1000(i32::from(record.advance_width), units_per_em)?;
        unicode_entries.push(ToUnicodeEntry {
            cid: record.cid,
            unicode: record.unicode.clone(),
        });
    }

    for run in runs {
        for unit in &run.plan.units {
            if !font
                .synthetic_glyphs
                .iter()
                .any(|record| record.cid == unit.cid)
            {
                return Err(PdfWriteError::MissingCidGlyph(unit.cid));
            }
        }
    }

    unicode_entries.sort_by_key(|entry| entry.cid);
    Ok(CidPdfResources {
        cid_to_gid,
        widths,
        to_unicode: build_to_unicode_cmap(&unicode_entries).into_bytes(),
    })
}

fn embed_type0_font(
    pdf: &mut PdfBuilder,
    font: &SynthesizedTrueTypeFont,
    resources: &CidPdfResources,
    metrics: TrueTypePdfMetrics,
    base_font: &str,
) -> Result<usize, PdfWriteError> {
    let type0_font = pdf.reserve();
    let cid_font = pdf.reserve();
    let descriptor = pdf.reserve();
    let font_dictionary = format!("/Length1 {} ", font.bytes.len());
    let font_file = pdf.add_stream(font_dictionary.as_bytes(), &font.bytes)?;
    let cid_to_gid_obj = pdf.add_stream(&[], &resources.cid_to_gid)?;
    let to_unicode_obj = pdf.add_stream(&[], &resources.to_unicode)?;
    let width_array = build_width_array(&resources.widths);
    let font_bbox = format!(
        "[{} {} {} {}]",
        metrics.x_min, metrics.y_min, metrics.x_max, metrics.y_max
    );

    pdf.set(
        descriptor,
        format!(
            "<< /Type /FontDescriptor /FontName /{base_font} /Flags 4 /FontBBox {font_bbox} \
             /ItalicAngle {} /Ascent {} /Descent {} /CapHeight {} /StemV 80 \
             /FontFile2 {font_file} 0 R >>",
            metrics.italic_angle, metrics.ascent, metrics.descent, metrics.cap_height
        )
        .into_bytes(),
    );
    pdf.set(
        cid_font,
        format!(
            "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{base_font} \
             /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> \
             /FontDescriptor {descriptor} 0 R /DW 1000 /W {width_array} \
             /CIDToGIDMap {cid_to_gid_obj} 0 R >>"
        )
        .into_bytes(),
    );
    pdf.set(
        type0_font,
        format!(
            "<< /Type /Font /Subtype /Type0 /BaseFont /{base_font} /Encoding /Identity-H \
             /DescendantFonts [{cid_font} 0 R] /ToUnicode {to_unicode_obj} 0 R >>"
        )
        .into_bytes(),
    );
    Ok(type0_font)
}

fn validate_options(units_per_em: u32, options: &Type0PdfOptions) -> Result<(), PdfWriteError> {
    if units_per_em == 0 {
        return Err(PdfWriteError::InvalidUnitsPerEm);
    }
    for (name, value) in [
        ("page_width", options.page_width),
        ("page_height", options.page_height),
        ("font_size", options.font_size),
    ] {
        if !value.is_finite() || value <= 0.0 {
            return Err(PdfWriteError::InvalidMetric(name));
        }
    }
    Ok(())
}

fn build_content_stream(
    runs: &[PlacedTextRun<'_>],
    units_per_em: u32,
    options: &Type0PdfOptions,
    marked_runs: &[MarkedRun],
) -> Result<Vec<u8>, PdfWriteError> {
    let scale = options.font_size / f64::from(units_per_em);
    let mut content = String::new();
    content.push_str("BT\n");
    let _ = writeln!(content, "/F0 {} Tf", pdf_number(options.font_size));

    for (run_index, run) in runs.iter().enumerate() {
        if !run.run_origin_x.is_finite() || !run.baseline_y.is_finite() {
            return Err(PdfWriteError::InvalidMetric("run placement"));
        }
        if run.plan.units.is_empty() {
            continue;
        }

        if options.tagged {
            let marked = marked_runs
                .iter()
                .find(|marked| marked.run_index == run_index)
                .ok_or(PdfWriteError::DocumentTooLarge)?;
            let _ = writeln!(content, "/Span << /MCID {} >> BDC", marked.mcid);
        }

        let run_actual_text = matches!(options.actual_text, ActualTextPolicy::AllRuns)
            || matches!(options.actual_text, ActualTextPolicy::RtlRuns)
                && run.direction == TextDirection::RightToLeft;
        if run_actual_text {
            let _ = writeln!(
                content,
                "/Span << /ActualText {} >> BDC",
                pdf_text_string(&run.plan.extracted_text())
            );
        }

        for unit in &run.plan.units {
            let unit_actual_text = matches!(options.actual_text, ActualTextPolicy::ComplexUnits)
                && unit.unicode.chars().count() > 1;
            if unit_actual_text {
                let _ = writeln!(
                    content,
                    "/Span << /ActualText {} >> BDC",
                    pdf_text_string(&unit.unicode)
                );
            }
            emit_positioned_cid(&mut content, unit, run, scale);
            if unit_actual_text {
                content.push_str("EMC\n");
            }
        }

        if run_actual_text {
            content.push_str("EMC\n");
        }
        if options.tagged {
            content.push_str("EMC\n");
        }
    }
    content.push_str("ET\n");
    Ok(content.into_bytes())
}

fn emit_positioned_cid(
    content: &mut String,
    unit: &PlannedUnit,
    run: &PlacedTextRun<'_>,
    scale: f64,
) {
    let x = run.run_origin_x + f64::from(unit.visual_x) * scale;
    let y = run.baseline_y;
    let _ = writeln!(
        content,
        "1 0 0 1 {} {} Tm <{:04X}> Tj",
        pdf_number(x),
        pdf_number(y),
        unit.cid.0
    );
}

fn build_width_array(widths: &[i32]) -> String {
    if widths.len() <= 1 {
        return "[]".to_owned();
    }
    let mut output = String::from("[1 [");
    for (index, width) in widths.iter().enumerate().skip(1) {
        if index > 1 {
            output.push(' ');
        }
        let _ = write!(output, "{width}");
    }
    output.push_str("]]");
    output
}

fn scale_1000(value: i32, units_per_em: u32) -> Result<i32, PdfWriteError> {
    let numerator = i64::from(value) * 1000;
    let denominator = i64::from(units_per_em);
    let rounded = if numerator >= 0 {
        (numerator + denominator / 2) / denominator
    } else {
        (numerator - denominator / 2) / denominator
    };
    i32::try_from(rounded).map_err(|_| PdfWriteError::InvalidMetric("font width"))
}

fn pdf_name(name: &str) -> String {
    let mut output = String::new();
    for byte in name.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'+') {
            output.push(char::from(byte));
        } else {
            let _ = write!(output, "#{byte:02X}");
        }
    }
    if output.is_empty() {
        "UnicodePdfSynthetic".to_owned()
    } else {
        output
    }
}

fn pdf_number(value: f64) -> String {
    let mut text = format!("{value:.4}");
    while text.contains('.') && text.ends_with('0') {
        text.pop();
    }
    if text.ends_with('.') {
        text.pop();
    }
    if text == "-0" {
        "0".to_owned()
    } else {
        text
    }
}

#[derive(Clone, Copy, Debug)]
struct TrueTypePdfMetrics {
    x_min: i32,
    y_min: i32,
    x_max: i32,
    y_max: i32,
    ascent: i32,
    descent: i32,
    cap_height: i32,
    italic_angle: i32,
}

impl TrueTypePdfMetrics {
    fn parse(font: &[u8], units_per_em: u32) -> Result<Self, PdfWriteError> {
        if font.len() < 12 || font.get(..4) == Some(b"ttcf") {
            return Err(PdfWriteError::InvalidTrueType("unsupported sfnt header"));
        }
        let num_tables = usize::from(read_u16(font, 4)?);
        let mut head = None;
        let mut hhea = None;
        let mut os2 = None;
        let mut post = None;

        for index in 0..num_tables {
            let base = 12 + index * 16;
            let tag = font
                .get(base..base + 4)
                .ok_or(PdfWriteError::InvalidTrueType("truncated table directory"))?;
            let offset = usize::try_from(read_u32(font, base + 8)?)
                .map_err(|_| PdfWriteError::InvalidTrueType("table offset too large"))?;
            let length = usize::try_from(read_u32(font, base + 12)?)
                .map_err(|_| PdfWriteError::InvalidTrueType("table length too large"))?;
            let end = offset
                .checked_add(length)
                .ok_or(PdfWriteError::DocumentTooLarge)?;
            let table = font
                .get(offset..end)
                .ok_or(PdfWriteError::InvalidTrueType("table extends past font"))?;
            match tag {
                b"head" => head = Some(table),
                b"hhea" => hhea = Some(table),
                b"OS/2" => os2 = Some(table),
                b"post" => post = Some(table),
                _ => {}
            }
        }

        let head = head.ok_or(PdfWriteError::InvalidTrueType("missing head table"))?;
        let hhea = hhea.ok_or(PdfWriteError::InvalidTrueType("missing hhea table"))?;
        if head.len() < 44 || hhea.len() < 8 {
            return Err(PdfWriteError::InvalidTrueType(
                "font metrics table is too short",
            ));
        }

        let x_min = scale_1000(i32::from(read_i16(head, 36)?), units_per_em)?;
        let y_min = scale_1000(i32::from(read_i16(head, 38)?), units_per_em)?;
        let x_max = scale_1000(i32::from(read_i16(head, 40)?), units_per_em)?;
        let y_max = scale_1000(i32::from(read_i16(head, 42)?), units_per_em)?;
        let ascent = scale_1000(i32::from(read_i16(hhea, 4)?), units_per_em)?;
        let descent = scale_1000(i32::from(read_i16(hhea, 6)?), units_per_em)?;
        let cap_height = os2
            .and_then(|table| {
                if table.len() >= 90 && read_u16(table, 0).ok()? >= 2 {
                    read_i16(table, 88).ok()
                } else {
                    None
                }
            })
            .map_or(ascent, |value| {
                scale_1000(i32::from(value), units_per_em).unwrap_or(ascent)
            });
        let italic_angle = post
            .and_then(|table| read_i32(table, 4).ok())
            .map_or(0, |fixed| fixed >> 16);

        Ok(Self {
            x_min,
            y_min,
            x_max,
            y_max,
            ascent,
            descent,
            cap_height,
            italic_angle,
        })
    }
}

fn read_u16(data: &[u8], offset: usize) -> Result<u16, PdfWriteError> {
    let bytes: [u8; 2] = data
        .get(offset..offset + 2)
        .ok_or(PdfWriteError::InvalidTrueType("truncated u16"))?
        .try_into()
        .map_err(|_| PdfWriteError::InvalidTrueType("invalid u16"))?;
    Ok(u16::from_be_bytes(bytes))
}

fn read_i16(data: &[u8], offset: usize) -> Result<i16, PdfWriteError> {
    let bytes: [u8; 2] = data
        .get(offset..offset + 2)
        .ok_or(PdfWriteError::InvalidTrueType("truncated i16"))?
        .try_into()
        .map_err(|_| PdfWriteError::InvalidTrueType("invalid i16"))?;
    Ok(i16::from_be_bytes(bytes))
}

fn read_u32(data: &[u8], offset: usize) -> Result<u32, PdfWriteError> {
    let bytes: [u8; 4] = data
        .get(offset..offset + 4)
        .ok_or(PdfWriteError::InvalidTrueType("truncated u32"))?
        .try_into()
        .map_err(|_| PdfWriteError::InvalidTrueType("invalid u32"))?;
    Ok(u32::from_be_bytes(bytes))
}

fn read_i32(data: &[u8], offset: usize) -> Result<i32, PdfWriteError> {
    let bytes: [u8; 4] = data
        .get(offset..offset + 4)
        .ok_or(PdfWriteError::InvalidTrueType("truncated i32"))?
        .try_into()
        .map_err(|_| PdfWriteError::InvalidTrueType("invalid i32"))?;
    Ok(i32::from_be_bytes(bytes))
}

#[derive(Debug, Default)]
struct PdfBuilder {
    objects: Vec<Option<Vec<u8>>>,
}

impl PdfBuilder {
    fn new() -> Self {
        Self::default()
    }

    fn reserve(&mut self) -> usize {
        self.objects.push(None);
        self.objects.len()
    }

    fn set(&mut self, id: usize, body: Vec<u8>) {
        self.objects[id - 1] = Some(body);
    }

    fn add_stream(
        &mut self,
        dictionary_entries: &[u8],
        stream: &[u8],
    ) -> Result<usize, PdfWriteError> {
        let length = u32::try_from(stream.len()).map_err(|_| PdfWriteError::DocumentTooLarge)?;
        let mut body = Vec::with_capacity(dictionary_entries.len() + stream.len() + 64);
        body.extend_from_slice(b"<< ");
        body.extend_from_slice(dictionary_entries);
        let _ = write!(
            body_string_adapter(&mut body),
            "/Length {length} >>\nstream\n"
        );
        body.extend_from_slice(stream);
        body.extend_from_slice(b"\nendstream");
        let id = self.reserve();
        self.set(id, body);
        Ok(id)
    }

    fn finish(self, root: usize) -> Result<Vec<u8>, PdfWriteError> {
        let mut output = b"%PDF-1.7\n%\xE2\xE3\xCF\xD3\n".to_vec();
        let mut offsets = Vec::with_capacity(self.objects.len() + 1);
        offsets.push(0_u64);

        for (index, object) in self.objects.into_iter().enumerate() {
            let body = object.ok_or(PdfWriteError::DocumentTooLarge)?;
            offsets.push(u64::try_from(output.len()).map_err(|_| PdfWriteError::DocumentTooLarge)?);
            let header = format!("{} 0 obj\n", index + 1);
            output.extend_from_slice(header.as_bytes());
            output.extend_from_slice(&body);
            output.extend_from_slice(b"\nendobj\n");
        }

        let xref = u64::try_from(output.len()).map_err(|_| PdfWriteError::DocumentTooLarge)?;
        let count = offsets.len();
        let _ = write!(
            body_string_adapter(&mut output),
            "xref\n0 {count}\n0000000000 65535 f \n"
        );
        for offset in offsets.into_iter().skip(1) {
            if offset > 9_999_999_999 {
                return Err(PdfWriteError::DocumentTooLarge);
            }
            let _ = writeln!(body_string_adapter(&mut output), "{offset:010} 00000 n ");
        }
        let _ = write!(
            body_string_adapter(&mut output),
            "trailer\n<< /Size {count} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
        );
        Ok(output)
    }
}

struct ByteFmt<'a>(&'a mut Vec<u8>);

impl fmt::Write for ByteFmt<'_> {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.0.extend_from_slice(s.as_bytes());
        Ok(())
    }
}

fn body_string_adapter(buffer: &mut Vec<u8>) -> ByteFmt<'_> {
    ByteFmt(buffer)
}

#[cfg(test)]
mod tests {
    use unicode_pdf_core::{FontId, LogicalPdfUnit, PositionedGlyph};
    use unicode_pdf_font::CidAllocator;

    use super::*;

    fn unit(text: &str, glyph_id: u32) -> LogicalPdfUnit {
        LogicalPdfUnit {
            unicode: text.to_owned(),
            source_range: None,
            font_id: FontId(1),
            glyphs: vec![PositionedGlyph {
                glyph_id,
                run_x: 0,
                run_y: 0,
                x_offset: 0,
                y_offset: 0,
                x_advance: 500,
                y_advance: 0,
            }],
        }
    }

    #[test]
    fn utf16be_supports_bmp_and_supplementary_unicode() {
        assert_eq!(utf16be_hex("ក"), "1780");
        assert_eq!(utf16be_hex("😀"), "D83DDE00");
    }

    #[test]
    fn cmap_maps_multi_codepoint_cluster_once() {
        let cmap = build_to_unicode_cmap(&[ToUnicodeEntry {
            cid: Cid(42),
            unicode: "ខ្ញុំ".to_owned(),
        }]);
        assert!(cmap.contains("<002A> <178117D2178917BB17C6>"));
    }

    #[test]
    fn same_unicode_can_have_multiple_visual_cids() {
        let units = vec![unit("ب", 102), unit("ب", 104), unit("ب", 101)];
        let mut allocator = CidAllocator::new();
        let plan = plan_text_run(&units, &mut allocator).unwrap();
        assert_eq!(plan.to_unicode.len(), 3);
        assert_eq!(plan.extracted_text(), "ببب");
        assert!(plan.to_unicode.iter().all(|entry| entry.unicode == "ب"));
    }

    #[test]
    fn logical_order_is_preserved_by_plan() {
        let source = "កម្ពុជា ខ្ញុំ";
        let units = vec![
            unit("ក", 1),
            unit("ម្ពុ", 2),
            unit("ជា", 3),
            unit(" ", 0),
            unit("ខ្ញុំ", 4),
        ];
        let mut allocator = CidAllocator::new();
        let plan = plan_text_run(&units, &mut allocator).unwrap();
        assert_eq!(plan.extracted_text(), source);
    }

    #[test]
    fn pdf_name_escapes_unsafe_bytes() {
        assert_eq!(pdf_name("A B/C"), "A#20B#2FC");
    }

    #[test]
    fn width_scaling_rounds_to_pdf_units() {
        assert_eq!(scale_1000(1024, 2048).unwrap(), 500);
        assert_eq!(scale_1000(500, 1000).unwrap(), 500);
    }

    #[test]
    fn width_array_uses_valid_contiguous_cid_syntax() {
        assert_eq!(build_width_array(&[0, 500, 600]), "[1 [500 600]]");
    }

    #[test]
    fn content_keeps_logical_order_while_using_visual_positions() {
        let plan = TextPlan {
            units: vec![
                PlannedUnit {
                    cid: Cid(1),
                    unicode: "ا".to_owned(),
                    visual_x: 500,
                    visual_end_x: 1000,
                },
                PlannedUnit {
                    cid: Cid(2),
                    unicode: "ب".to_owned(),
                    visual_x: 0,
                    visual_end_x: 500,
                },
            ],
            to_unicode: Vec::new(),
        };
        let run = PlacedTextRun {
            plan: &plan,
            run_origin_x: 100.0,
            baseline_y: 700.0,
            direction: TextDirection::RightToLeft,
            language: Some("und-Arab"),
            paragraph_index: 0,
        };
        let options = Type0PdfOptions {
            font_size: 10.0,
            tagged: false,
            actual_text: ActualTextPolicy::Off,
            ..Type0PdfOptions::default()
        };
        let content =
            String::from_utf8(build_content_stream(&[run], 1000, &options, &[]).unwrap()).unwrap();
        let first = content.find("<0001> Tj").unwrap();
        let second = content.find("<0002> Tj").unwrap();
        assert!(first < second);
        assert!(content.contains("1 0 0 1 105 700 Tm <0001> Tj"));
        assert!(content.contains("1 0 0 1 100 700 Tm <0002> Tj"));
    }
    #[test]
    fn tagged_content_assigns_mcid_and_scopes_complex_actual_text() {
        let units = vec![unit("ខ្ញុំ", 4)];
        let mut allocator = CidAllocator::new();
        let plan = plan_text_run(&units, &mut allocator).unwrap();
        let run = PlacedTextRun {
            plan: &plan,
            run_origin_x: 100.0,
            baseline_y: 700.0,
            direction: TextDirection::LeftToRight,
            language: Some("und-Khmr"),
            paragraph_index: 7,
        };
        let options = Type0PdfOptions {
            font_size: 10.0,
            tagged: true,
            actual_text: ActualTextPolicy::ComplexUnits,
            ..Type0PdfOptions::default()
        };
        let marked = build_marked_runs(&[run]).unwrap();
        let content =
            String::from_utf8(build_content_stream(&[run], 1000, &options, &marked).unwrap())
                .unwrap();
        assert!(content.contains("/Span << /MCID 0 >> BDC"));
        assert!(content.contains("/ActualText <FEFF178117D2178917BB17C6>"));
        assert_eq!(marked[0].paragraph_index, 7);
    }

    #[test]
    fn structure_tree_groups_directional_spans_under_one_paragraph() {
        let plan = TextPlan {
            units: vec![PlannedUnit {
                cid: Cid(1),
                unicode: "ا".to_owned(),
                visual_x: 0,
                visual_end_x: 500,
            }],
            to_unicode: Vec::new(),
        };
        let runs = [PlacedTextRun {
            plan: &plan,
            run_origin_x: 100.0,
            baseline_y: 700.0,
            direction: TextDirection::RightToLeft,
            language: Some("und-Arab"),
            paragraph_index: 0,
        }];
        let marked = build_marked_runs(&runs).unwrap();
        let mut pdf = PdfBuilder::new();
        let page = pdf.reserve();
        let tagging = embed_structure_tree(&mut pdf, page, &marked, "und").unwrap();
        let root =
            String::from_utf8(pdf.objects[tagging.struct_tree_root - 1].clone().unwrap()).unwrap();
        let all_objects = pdf
            .objects
            .iter()
            .filter_map(|object| object.as_ref())
            .flat_map(|object| object.iter().copied())
            .collect::<Vec<_>>();
        let all = String::from_utf8(all_objects).unwrap();
        assert!(root.contains("/Type /StructTreeRoot"));
        assert!(all.contains("/S /P"));
        assert!(all.contains("/S /Span"));
        assert!(all.contains("/WritingMode /RlTb"));
        assert!(all.contains("/Lang <FEFF0075006E0064002D0041007200610062>"));
    }

    #[test]
    fn document_mcids_restart_on_each_page() {
        let plan = TextPlan {
            units: vec![PlannedUnit {
                cid: Cid(1),
                unicode: "A".to_owned(),
                visual_x: 0,
                visual_end_x: 500,
            }],
            to_unicode: Vec::new(),
        };
        let runs = [
            DocumentPlacedTextRun {
                plan: &plan,
                font_index: 0,
                page_index: 0,
                run_origin_x: 50.0,
                baseline_y: 700.0,
                font_size: 12.0,
                direction: TextDirection::LeftToRight,
                language: Some("und-Latn"),
                paragraph_index: 0,
            },
            DocumentPlacedTextRun {
                plan: &plan,
                font_index: 0,
                page_index: 1,
                run_origin_x: 50.0,
                baseline_y: 700.0,
                font_size: 12.0,
                direction: TextDirection::LeftToRight,
                language: Some("und-Latn"),
                paragraph_index: 0,
            },
        ];
        let marked = build_document_marked_runs(&runs, 2).unwrap();
        assert_eq!(marked[0].mcid, 0);
        assert_eq!(marked[1].mcid, 0);
        assert_eq!(marked[0].page_index, 0);
        assert_eq!(marked[1].page_index, 1);
    }

    #[test]
    fn document_ltr_content_uses_contiguous_cid_string() {
        let plan = TextPlan {
            units: vec![
                PlannedUnit {
                    cid: Cid(1),
                    unicode: "A".to_owned(),
                    visual_x: 0,
                    visual_end_x: 500,
                },
                PlannedUnit {
                    cid: Cid(2),
                    unicode: "B".to_owned(),
                    visual_x: 500,
                    visual_end_x: 1000,
                },
            ],
            to_unicode: Vec::new(),
        };
        let synthetic = SynthesizedTrueTypeFont {
            bytes: Vec::new(),
            base_glyph_count: 0,
            synthetic_glyphs: Vec::new(),
        };
        let fonts = [EmbeddedType0Font {
            font: &synthetic,
            units_per_em: 1000,
            base_font_name: "Test".to_owned(),
        }];
        let runs = [DocumentPlacedTextRun {
            plan: &plan,
            font_index: 0,
            page_index: 0,
            run_origin_x: 100.0,
            baseline_y: 700.0,
            font_size: 10.0,
            direction: TextDirection::LeftToRight,
            language: Some("und-Latn"),
            paragraph_index: 0,
        }];
        let options = Type0DocumentOptions {
            tagged: false,
            actual_text: ActualTextPolicy::Off,
            ..Type0DocumentOptions::default()
        };
        let content = String::from_utf8(
            build_document_page_content(0, &runs, &fonts, &options, &[]).unwrap(),
        )
        .unwrap();
        assert!(content.contains("1 0 0 1 100 700 Tm <00010002> Tj"));
        assert_eq!(content.matches(" Tj").count(), 1);
    }

    #[test]
    fn page_fragment_text_joins_visual_lines_without_newlines() {
        let first = TextPlan {
            units: vec![PlannedUnit {
                cid: Cid(1),
                unicode: "abc".to_owned(),
                visual_x: 0,
                visual_end_x: 500,
            }],
            to_unicode: Vec::new(),
        };
        let second = TextPlan {
            units: vec![PlannedUnit {
                cid: Cid(2),
                unicode: "def".to_owned(),
                visual_x: 0,
                visual_end_x: 500,
            }],
            to_unicode: Vec::new(),
        };
        let runs = [
            DocumentPlacedTextRun {
                plan: &first,
                font_index: 0,
                page_index: 0,
                run_origin_x: 10.0,
                baseline_y: 700.0,
                font_size: 10.0,
                direction: TextDirection::LeftToRight,
                language: Some("und-Latn"),
                paragraph_index: 4,
            },
            DocumentPlacedTextRun {
                plan: &second,
                font_index: 0,
                page_index: 0,
                run_origin_x: 10.0,
                baseline_y: 680.0,
                font_size: 10.0,
                direction: TextDirection::LeftToRight,
                language: Some("und-Latn"),
                paragraph_index: 4,
            },
        ];
        assert_eq!(page_paragraph_fragment_text(0, 4, &runs), "abcdef");
    }

    #[test]
    fn document_structure_can_carry_authoritative_paragraph_actual_text() {
        let marked = [DocumentMarkedRun {
            run_index: 0,
            page_index: 0,
            mcid: 0,
            paragraph_index: 9,
            direction: TextDirection::LeftToRight,
            language: Some("und-Khmr".to_owned()),
        }];
        let paragraph = DocumentParagraphText {
            paragraph_index: 9,
            unicode: "ខ្មែរ".to_owned(),
            terminated_by_newline: false,
        };
        let mut pdf = PdfBuilder::new();
        let page = pdf.reserve();
        let tagging = embed_document_structure_tree(
            &mut pdf,
            &[page],
            &marked,
            "und-Khmr",
            &[paragraph],
            ParagraphTextPolicy::StructureActualText,
        )
        .unwrap();
        let all_objects = pdf
            .objects
            .iter()
            .filter_map(|object| object.as_ref())
            .flat_map(|object| object.iter().copied())
            .collect::<Vec<_>>();
        let all = String::from_utf8(all_objects).unwrap();
        assert!(all.contains("/ActualText <FEFF178117D2179817C2179A>"));
        assert!(all.contains("/S /P"));
        assert!(tagging.struct_tree_root > 0);
    }
}
