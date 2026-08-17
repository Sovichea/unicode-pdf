//! Bidirectional-text analysis abstraction for `unicode-pdf`.
//!
//! PDF semantic order and page geometry are deliberately separate. This crate
//! describes directional runs in logical source order while retaining enough
//! information for a layout layer to place those runs in visual order.

use std::fmt;

use unicode_pdf_core::{SourceRange, TextDirection};

/// One resolved directional run inside a logical paragraph.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BidiRun {
    /// UTF-8 byte range in the original paragraph.
    pub source_range: SourceRange,
    /// Resolved embedding level from the Unicode Bidirectional Algorithm.
    pub level: u8,
    /// Direction implied by the embedding level.
    pub direction: TextDirection,
    /// Left-to-right visual ordering rank among resolved runs.
    pub visual_order: usize,
}

/// Resolved `BiDi` information for one logical paragraph.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BidiParagraph {
    /// Resolved paragraph base direction.
    pub base_direction: TextDirection,
    /// Directional runs in logical source order.
    pub runs: Vec<BidiRun>,
}

impl BidiParagraph {
    /// Verifies that the run ranges form a contiguous partition of `text`.
    ///
    /// # Errors
    ///
    /// Returns [`BidiError::InvalidRunPartition`] when a range is not on a
    /// UTF-8 boundary, overlaps another range, leaves a gap, or does not cover
    /// the complete paragraph.
    pub fn validate(&self, text: &str) -> Result<(), BidiError> {
        let mut cursor = 0_usize;
        for run in &self.runs {
            if run.source_range.start() != cursor
                || run.source_range.end() > text.len()
                || !text.is_char_boundary(run.source_range.start())
                || !text.is_char_boundary(run.source_range.end())
            {
                return Err(BidiError::InvalidRunPartition);
            }
            cursor = run.source_range.end();
        }
        if cursor != text.len() {
            return Err(BidiError::InvalidRunPartition);
        }
        Ok(())
    }
}

/// A backend that resolves Unicode bidirectional runs.
pub trait BidiResolver {
    /// Resolves `text` using the Unicode Bidirectional Algorithm.
    ///
    /// # Errors
    ///
    /// Returns [`BidiError`] if the backend is unavailable or produces an
    /// invalid partition.
    fn resolve(&self, text: &str) -> Result<BidiParagraph, BidiError>;
}

/// Errors produced by `BiDi` backends.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BidiError {
    /// The requested backend cannot be loaded on this system.
    BackendUnavailable(String),
    /// The paragraph is too large for the backend ABI.
    InputTooLarge,
    /// The backend reported failure.
    BackendFailure,
    /// Resolved runs do not form a valid UTF-8 partition.
    InvalidRunPartition,
}

impl fmt::Display for BidiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BackendUnavailable(message) => write!(f, "BiDi backend unavailable: {message}"),
            Self::InputTooLarge => write!(f, "BiDi input exceeds backend limits"),
            Self::BackendFailure => write!(f, "BiDi backend failed to resolve the paragraph"),
            Self::InvalidRunPartition => write!(f, "BiDi runs do not partition the paragraph"),
        }
    }
}

impl std::error::Error for BidiError {}
