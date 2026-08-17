//! Text-shaping abstraction for `unicode-pdf`.
//!
//! The PDF model depends only on this crate's safe trait. Concrete shaping
//! engines live in adapter crates so producers can choose `HarfBuzz`, `HarfRust`,
//! platform shaping APIs, or another implementation without changing the PDF
//! text model.

use std::fmt;

use unicode_pdf_core::{FontId, LogicalTextRun, TextDirection};

/// Input options for one shaping operation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ShapeOptions {
    /// Logical font identity to attach to the produced units.
    pub font_id: FontId,
    /// Face index in a TrueType/OpenType collection.
    pub face_index: u32,
    /// Direction resolved by the `BiDi` layer. `Auto` lets the shaper guess.
    pub direction: TextDirection,
}

impl ShapeOptions {
    /// Creates options for the first face in a font.
    #[must_use]
    pub const fn new(font_id: FontId) -> Self {
        Self {
            font_id,
            face_index: 0,
            direction: TextDirection::Auto,
        }
    }

    /// Returns options with an explicitly resolved text direction.
    #[must_use]
    pub const fn with_direction(mut self, direction: TextDirection) -> Self {
        self.direction = direction;
        self
    }
}

/// Result of shaping one complete logical text run.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ShapeOutput {
    /// Units-per-em reported by the shaping font.
    pub units_per_em: u32,
    /// Logical Unicode and shaped visual components.
    pub run: LogicalTextRun,
}

/// A shaping engine capable of preserving source-cluster relationships.
pub trait TextShaper {
    /// Shapes `text` using `font_data` while preserving the original logical
    /// Unicode through source-cluster byte offsets.
    ///
    /// # Errors
    ///
    /// Returns [`ShapeError`] if the font cannot be opened, the shaping engine
    /// is unavailable, or its cluster output cannot be converted into the
    /// logical PDF model.
    fn shape(
        &self,
        font_data: &[u8],
        text: &str,
        options: ShapeOptions,
    ) -> Result<ShapeOutput, ShapeError>;
}

/// Errors produced by shaping backends.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ShapeError {
    /// The requested shaping engine is not available on this system.
    BackendUnavailable(String),
    /// The font data could not be opened by the shaping engine.
    InvalidFont,
    /// The input exceeds a backend size limit.
    InputTooLarge,
    /// A shaping coordinate or accumulated pen position overflowed the model.
    CoordinateOverflow,
    /// The backend returned an invalid or unsupported direction.
    UnsupportedDirection(u32),
    /// The shaping backend returned inconsistent glyph arrays.
    InvalidBackendOutput,
    /// Logical cluster reconstruction failed.
    LogicalModel(String),
}

impl fmt::Display for ShapeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BackendUnavailable(message) => {
                write!(f, "shaping backend unavailable: {message}")
            }
            Self::InvalidFont => write!(f, "shaping backend could not open the font"),
            Self::InputTooLarge => write!(f, "shaping input exceeds backend limits"),
            Self::CoordinateOverflow => write!(f, "shaping coordinates exceed supported range"),
            Self::UnsupportedDirection(direction) => {
                write!(f, "unsupported shaping direction value {direction}")
            }
            Self::InvalidBackendOutput => write!(f, "shaping backend returned invalid output"),
            Self::LogicalModel(message) => write!(f, "logical text model error: {message}"),
        }
    }
}

impl std::error::Error for ShapeError {}
