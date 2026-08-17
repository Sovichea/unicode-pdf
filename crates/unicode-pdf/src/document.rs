//! High-level document API.

use std::fmt;

use crate::bidi::{BidiError, BidiResolver};
use crate::core::FontId;
use crate::font::{synthesize_truetype_composites, CidAllocator, FontError};
use crate::layout::{
    layout_document_with_break_opportunities, FontSet, LayoutError, LayoutFont, LayoutOptions,
};
use crate::pdf::{
    build_type0_document_pdf, plan_text_run, ActualTextPolicy, DocumentParagraphText,
    DocumentPlacedTextRun, EmbeddedType0Font, ParagraphTextPolicy, PdfWriteError,
    Type0DocumentOptions,
};
use crate::shape::{ShapeError, ShapeOptions, TextShaper};
use crate::{DefaultBidiResolver, DefaultShaper};

/// A font supplied by the application.
#[derive(Clone, Debug)]
pub struct Font {
    name: String,
    bytes: Vec<u8>,
}

impl Font {
    /// Creates a font from in-memory OpenType/TrueType bytes.
    #[must_use]
    pub fn from_bytes(name: impl Into<String>, bytes: impl Into<Vec<u8>>) -> Self {
        Self {
            name: name.into(),
            bytes: bytes.into(),
        }
    }

    /// Returns the application-visible font name.
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Returns the original font bytes.
    #[must_use]
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
}

/// Completed PDF output.
#[derive(Clone, Debug)]
pub struct PdfOutput {
    bytes: Vec<u8>,
}

impl PdfOutput {
    /// Borrows the serialized PDF bytes.
    #[must_use]
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// Consumes the output and returns the serialized PDF bytes.
    #[must_use]
    pub fn into_bytes(self) -> Vec<u8> {
        self.bytes
    }
}

/// Batteries-included text document builder.
#[derive(Clone, Debug)]
pub struct Document {
    text: String,
    fonts: Vec<Font>,
    break_opportunities: Vec<usize>,
    /// Page and paragraph layout metrics.
    pub layout: LayoutOptions,
    /// Whether to emit Tagged PDF structure.
    pub tagged: bool,
}

impl Default for Document {
    fn default() -> Self {
        Self::new()
    }
}

impl Document {
    /// Creates an empty A4 document with default layout metrics.
    #[must_use]
    pub fn new() -> Self {
        Self {
            text: String::new(),
            fonts: Vec::new(),
            break_opportunities: Vec::new(),
            layout: LayoutOptions::default(),
            tagged: true,
        }
    }

    /// Adds a font to the fallback chain. Earlier fonts have higher priority.
    pub fn add_font(&mut self, font: Font) {
        self.fonts.push(font);
    }

    /// Appends one logical paragraph. A hard source newline is inserted only
    /// between paragraphs explicitly added through this method.
    pub fn paragraph(&mut self, text: &str) -> &mut Self {
        if !self.text.is_empty() {
            self.text.push('\n');
        }
        self.text.push_str(text);
        self
    }

    /// Replaces the complete logical UTF-8 document text.
    pub fn set_text(&mut self, text: impl Into<String>) -> &mut Self {
        self.text = text.into();
        self
    }

    /// Returns the exact logical UTF-8 document text.
    #[must_use]
    pub fn text(&self) -> &str {
        &self.text
    }

    /// Supplies legal soft line-break byte offsets without modifying the
    /// logical Unicode string.
    pub fn set_break_opportunities(&mut self, offsets: impl Into<Vec<usize>>) -> &mut Self {
        self.break_opportunities = offsets.into();
        self
    }

    /// Compiles the document to PDF with the Cargo-selected default shaping and
    /// `BiDi` backends.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] for invalid fonts, missing glyph coverage, shaping,
    /// layout, font synthesis, or PDF serialization failures.
    pub fn finish(&self) -> Result<PdfOutput, Error> {
        let shaper = DefaultShaper::new().map_err(Error::Shape)?;
        let bidi = DefaultBidiResolver::new().map_err(Error::Bidi)?;
        self.finish_with(&shaper, &bidi)
    }

    /// Compiles the document with caller-supplied shaping and `BiDi` backends.
    ///
    /// This is the preferred integration point for applications that already
    /// own a shaping engine, want to compare `HarfRust` with system `HarfBuzz`, or
    /// need deterministic backend selection independent from Cargo features.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] for invalid fonts, missing glyph coverage, shaping,
    /// layout, font synthesis, or PDF serialization failures.
    #[allow(clippy::too_many_lines)]
    pub fn finish_with<S: TextShaper, B: BidiResolver>(
        &self,
        shaper: &S,
        bidi: &B,
    ) -> Result<PdfOutput, Error> {
        if self.fonts.is_empty() {
            return Err(Error::NoFonts);
        }

        let layout_fonts = self
            .fonts
            .iter()
            .enumerate()
            .map(|(index, font)| {
                let id = u32::try_from(index + 1).map_err(|_| Error::TooManyFonts)?;
                LayoutFont::new(FontId(id), font.name.clone(), font.bytes.clone())
                    .map_err(Error::Layout)
            })
            .collect::<Result<Vec<_>, Error>>()?;
        let font_set = FontSet::new(layout_fonts).map_err(Error::Layout)?;
        let layout = layout_document_with_break_opportunities(
            &self.text,
            &self.break_opportunities,
            &font_set,
            shaper,
            bidi,
            self.layout,
        )
        .map_err(Error::Layout)?;

        let mut allocators: Vec<CidAllocator> = font_set
            .fonts()
            .iter()
            .map(|_| CidAllocator::new())
            .collect();
        let mut plans = Vec::with_capacity(layout.runs.len());
        for run in &layout.runs {
            plans.push(
                plan_text_run(&run.units, &mut allocators[run.font_index]).map_err(Error::Font)?,
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
                .map_err(Error::Shape)?;
            let synthetic =
                synthesize_truetype_composites(&font.bytes, allocators[font_index].entries())
                    .map_err(Error::Font)?;
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
                let font_index =
                    original_to_resource[run.font_index].ok_or(Error::MissingFontResource {
                        font_index: run.font_index,
                    })?;
                Ok(DocumentPlacedTextRun {
                    plan,
                    font_index,
                    page_index: run.page_index,
                    run_origin_x: run.run_origin_x,
                    baseline_y: run.baseline_y,
                    font_size: run.font_size,
                    direction: run.direction,
                    language: Some(run.language),
                    paragraph_index: run.paragraph_index,
                })
            })
            .collect::<Result<_, Error>>()?;

        let options = Type0DocumentOptions {
            page_width: self.layout.page_width,
            page_height: self.layout.page_height,
            tagged: self.tagged,
            document_language: document_language(&layout.runs).to_owned(),
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
            paragraph_text: ParagraphTextPolicy::StructureActualText,
        };
        let bytes = build_type0_document_pdf(&embedded, layout.page_count, &placed, &options)
            .map_err(Error::Pdf)?;
        Ok(PdfOutput { bytes })
    }
}

/// Renders one logical paragraph with a single font using the default backends.
///
/// This convenience helper is intended for small applications and examples.
/// More advanced applications should use [`Document`] directly.
///
/// # Errors
///
/// Returns [`Error`] if shaping, layout, font synthesis, or PDF writing fails.
pub fn render_text(text: &str, font: Font) -> Result<PdfOutput, Error> {
    let mut document = Document::new();
    document.add_font(font);
    document.paragraph(text);
    document.finish()
}

fn document_language(runs: &[crate::layout::LayoutRun]) -> &'static str {
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

/// Errors returned by the high-level [`Document`] API.
#[derive(Debug)]
pub enum Error {
    /// No font was supplied.
    NoFonts,
    /// The fallback chain exceeds the current font identifier space.
    TooManyFonts,
    /// Shaping failed.
    Shape(ShapeError),
    /// Bidirectional analysis failed.
    Bidi(BidiError),
    /// Font planning or synthesis failed.
    Font(FontError),
    /// Paragraph layout failed.
    Layout(LayoutError),
    /// A laid-out font unexpectedly had no emitted PDF resource.
    MissingFontResource {
        /// Zero-based index in the internal fallback chain.
        font_index: usize,
    },
    /// PDF serialization failed.
    Pdf(PdfWriteError),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoFonts => write!(f, "at least one font is required"),
            Self::TooManyFonts => write!(f, "too many fallback fonts"),
            Self::Shape(error) => write!(f, "shaping failed: {error}"),
            Self::Bidi(error) => write!(f, "BiDi analysis failed: {error}"),
            Self::Font(error) => write!(f, "font processing failed: {error}"),
            Self::Layout(error) => write!(f, "layout failed: {error}"),
            Self::MissingFontResource { font_index } => {
                write!(
                    f,
                    "missing emitted PDF resource for fallback font {font_index}"
                )
            }
            Self::Pdf(error) => write!(f, "PDF serialization failed: {error}"),
        }
    }
}

impl std::error::Error for Error {}
