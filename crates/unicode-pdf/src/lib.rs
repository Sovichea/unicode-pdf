//! Unicode-correct PDF generation for Rust.
//!
//! The crate keeps logical Unicode independent from shaped glyph geometry. Its
//! default configuration uses `HarfRust` for shaping and `unicode-bidi` for the
//! Unicode Bidirectional Algorithm, so downstream applications do not need
//! native shaping libraries.
//!
//! Advanced users can enter at the logical/layout/PDF layers, while the
//! high-level [`Document`] API provides a batteries-included path for text PDFs.

pub mod bidi;
pub mod core;
pub mod document;
pub mod external;
pub mod font;
pub mod layout;
pub mod pdf;
pub mod shape;

pub use bidi::{BidiError, BidiParagraph, BidiResolver, BidiRun};
pub use core::{
    FontId, LogicalPdfUnit, LogicalTextRun, PositionedGlyph, ShapedGlyph, SourceRange,
    TextDirection,
};
pub use document::{render_text, Document, Error, Font, PdfOutput};
pub use external::{logical_units_from_external_glyphs, ExternalGlyph, ExternalTextError};
pub use font::{
    synthesize_truetype_composites, Cid, CidAllocator, FontCoverage, FontError,
    SynthesizedTrueTypeFont,
};
pub use layout::{
    layout_document, layout_document_with_break_opportunities, FontSet, GeometryIndex,
    LayoutDocument, LayoutError, LayoutFont, LayoutOptions, LogicalParagraph, PdfRect,
};
pub use pdf::{
    build_to_unicode_cmap, build_type0_document_pdf, build_type0_single_page_pdf, plan_text_run,
    ActualTextPolicy, DocumentParagraphText, DocumentPlacedTextRun, EmbeddedType0Font,
    ParagraphTextPolicy, PdfWriteError, PlacedTextRun, TextPlan, Type0DocumentOptions,
    Type0PdfOptions,
};
pub use shape::{ShapeError, ShapeOptions, ShapeOutput, TextShaper};

#[cfg(all(feature = "system-fribidi", unix))]
pub use bidi::system_fribidi::FriBidiResolver;
#[cfg(feature = "unicode-bidi")]
pub use bidi::unicode::UnicodeBidiResolver;
#[cfg(feature = "harfrust")]
pub use shape::harfrust::HarfRustShaper;
#[cfg(all(feature = "system-harfbuzz", unix))]
pub use shape::system_harfbuzz::HarfBuzzShaper;

/// Default shaping backend selected by Cargo features.
#[cfg(feature = "harfrust")]
pub type DefaultShaper = HarfRustShaper;
/// Default shaping backend when `HarfRust` is disabled and the system adapter is enabled.
#[cfg(all(not(feature = "harfrust"), feature = "system-harfbuzz", unix))]
pub type DefaultShaper = HarfBuzzShaper;

/// Default `BiDi` backend selected by Cargo features.
#[cfg(feature = "unicode-bidi")]
pub type DefaultBidiResolver = UnicodeBidiResolver;
/// Default `BiDi` backend when `unicode-bidi` is disabled and `FriBidi` is enabled.
#[cfg(all(not(feature = "unicode-bidi"), feature = "system-fribidi", unix))]
pub type DefaultBidiResolver = FriBidiResolver;

#[cfg(not(any(feature = "harfrust", all(feature = "system-harfbuzz", unix))))]
compile_error!("unicode-pdf requires either the `harfrust` feature or `system-harfbuzz` on Unix");

#[cfg(not(any(feature = "unicode-bidi", all(feature = "system-fribidi", unix))))]
compile_error!(
    "unicode-pdf requires either the `unicode-bidi` feature or `system-fribidi` on Unix"
);
