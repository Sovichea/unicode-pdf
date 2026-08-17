//! Core logical-text model for Unicode-correct PDF generation.
//!
//! The key invariant is that logical Unicode is preserved independently from
//! shaped glyph IDs and visual ordering.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::ops::Range;

/// Identifies a font or font instance inside a producer.
#[derive(Clone, Copy, Debug, Default, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct FontId(pub u32);

/// Direction reported for a logical text run.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TextDirection {
    /// Left-to-right text.
    LeftToRight,
    /// Right-to-left text.
    RightToLeft,
    /// Direction was not resolved by the caller.
    Auto,
}

/// Byte range in the producer's original UTF-8 source string.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceRange(pub Range<usize>);

impl SourceRange {
    /// Creates a validated source range.
    ///
    /// # Errors
    ///
    /// Returns [`CoreError::InvalidSourceRange`] when `start` is greater than `end`.
    pub fn new(start: usize, end: usize) -> Result<Self, CoreError> {
        if start > end {
            return Err(CoreError::InvalidSourceRange { start, end });
        }
        Ok(Self(start..end))
    }

    /// Returns the first byte offset in the range.
    #[must_use]
    pub fn start(&self) -> usize {
        self.0.start
    }

    /// Returns the exclusive end byte offset in the range.
    #[must_use]
    pub fn end(&self) -> usize {
        self.0.end
    }
}

/// One glyph produced by a shaping engine.
///
/// Coordinates are expressed in caller-defined font units. `run_x` and
/// `run_y` are visual pen positions inside the shaped run before offsets.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct PositionedGlyph {
    /// Font glyph identifier.
    pub glyph_id: u32,
    /// Visual pen X position in the shaped run.
    pub run_x: i32,
    /// Visual pen Y position in the shaped run.
    pub run_y: i32,
    /// Shaping X offset.
    pub x_offset: i32,
    /// Shaping Y offset.
    pub y_offset: i32,
    /// Shaping X advance.
    pub x_advance: i32,
    /// Shaping Y advance.
    pub y_advance: i32,
}

/// Raw shaped glyph plus the byte offset of the source cluster that produced it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ShapedGlyph {
    /// Font glyph identifier.
    pub glyph_id: u32,
    /// UTF-8 byte offset of the source cluster.
    pub cluster_start: usize,
    /// Visual pen X position in the complete shaped run.
    pub run_x: i32,
    /// Visual pen Y position in the complete shaped run.
    pub run_y: i32,
    /// Shaping X offset.
    pub x_offset: i32,
    /// Shaping Y offset.
    pub y_offset: i32,
    /// Shaping X advance.
    pub x_advance: i32,
    /// Shaping Y advance.
    pub y_advance: i32,
}

impl From<&ShapedGlyph> for PositionedGlyph {
    fn from(value: &ShapedGlyph) -> Self {
        Self {
            glyph_id: value.glyph_id,
            run_x: value.run_x,
            run_y: value.run_y,
            x_offset: value.x_offset,
            y_offset: value.y_offset,
            x_advance: value.x_advance,
            y_advance: value.y_advance,
        }
    }
}

/// One logical PDF text unit and the visual glyphs that render it.
///
/// `unicode` is authoritative. A PDF writer should generate `/ToUnicode` from
/// this field rather than trying to infer Unicode from the glyph IDs.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalPdfUnit {
    /// Exact logical Unicode represented by this unit.
    pub unicode: String,
    /// Optional original UTF-8 source range.
    pub source_range: Option<SourceRange>,
    /// Font or font instance used to render the unit.
    pub font_id: FontId,
    /// Visual glyph components produced by shaping.
    pub glyphs: Vec<PositionedGlyph>,
}

impl LogicalPdfUnit {
    /// Returns `true` when the unit is explicit logical whitespace.
    #[must_use]
    pub fn is_whitespace(&self) -> bool {
        !self.unicode.is_empty() && self.unicode.chars().all(char::is_whitespace)
    }
}

/// A complete shaped run whose units are stored in logical source order.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LogicalTextRun {
    /// Original logical Unicode for the run.
    pub original_text: String,
    /// Resolved or requested text direction.
    pub direction: TextDirection,
    /// Units stored in logical source order.
    pub units: Vec<LogicalPdfUnit>,
}

impl LogicalTextRun {
    /// Reconstructs the semantic string represented by the units.
    #[must_use]
    pub fn extracted_text(&self) -> String {
        self.units
            .iter()
            .map(|unit| unit.unicode.as_str())
            .collect()
    }

    /// Verifies that logical unit text exactly reconstructs the input text.
    ///
    /// # Errors
    ///
    /// Returns [`CoreError::LogicalTextMismatch`] if concatenating the logical
    /// units does not reproduce `original_text` exactly.
    pub fn validate_round_trip(&self) -> Result<(), CoreError> {
        let reconstructed = self.extracted_text();
        if reconstructed == self.original_text {
            Ok(())
        } else {
            Err(CoreError::LogicalTextMismatch {
                expected: self.original_text.clone(),
                actual: reconstructed,
            })
        }
    }
}

/// Groups shaped glyphs by their source cluster and returns units in logical
/// source order even if the shaping engine returned glyphs in visual RTL order.
///
/// The caller is responsible for configuring its shaping engine so cluster
/// values refer to UTF-8 byte offsets in `text`.
///
/// # Errors
///
/// Returns [`CoreError`] when cluster offsets are invalid UTF-8 boundaries,
/// the initial cluster is missing, or the reconstructed logical text differs
/// from the input.
pub fn logical_units_from_shaped_glyphs(
    text: &str,
    font_id: FontId,
    glyphs: &[ShapedGlyph],
) -> Result<Vec<LogicalPdfUnit>, CoreError> {
    if text.is_empty() {
        return Ok(Vec::new());
    }

    let mut starts = BTreeSet::new();
    for glyph in glyphs {
        validate_cluster_boundary(text, glyph.cluster_start)?;
        starts.insert(glyph.cluster_start);
    }

    if starts.is_empty() {
        return Err(CoreError::MissingClustersForNonEmptyText);
    }
    if !starts.contains(&0) {
        return Err(CoreError::MissingInitialCluster);
    }

    let mut glyphs_by_start: BTreeMap<usize, Vec<PositionedGlyph>> = BTreeMap::new();
    for glyph in glyphs {
        glyphs_by_start
            .entry(glyph.cluster_start)
            .or_default()
            .push(PositionedGlyph::from(glyph));
    }

    let ordered_starts: Vec<usize> = starts.into_iter().collect();
    let mut units = Vec::with_capacity(ordered_starts.len());

    for (index, start) in ordered_starts.iter().copied().enumerate() {
        let end = ordered_starts.get(index + 1).copied().unwrap_or(text.len());
        validate_cluster_boundary(text, end)?;
        let unicode = text
            .get(start..end)
            .ok_or(CoreError::InvalidUtf8Boundary(end))?
            .to_owned();

        units.push(LogicalPdfUnit {
            unicode,
            source_range: Some(SourceRange(start..end)),
            font_id,
            glyphs: glyphs_by_start.remove(&start).unwrap_or_default(),
        });
    }

    let run = LogicalTextRun {
        original_text: text.to_owned(),
        direction: TextDirection::Auto,
        units: units.clone(),
    };
    run.validate_round_trip()?;

    Ok(units)
}

fn validate_cluster_boundary(text: &str, offset: usize) -> Result<(), CoreError> {
    if offset > text.len() || !text.is_char_boundary(offset) {
        return Err(CoreError::InvalidUtf8Boundary(offset));
    }
    Ok(())
}

/// Errors produced by the logical text model.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CoreError {
    /// A source range started after its end.
    InvalidSourceRange {
        /// First byte offset of the invalid range.
        start: usize,
        /// Exclusive end byte offset of the invalid range.
        end: usize,
    },
    /// A shaping cluster did not land on a UTF-8 boundary.
    InvalidUtf8Boundary(usize),
    /// A non-empty source string had no shaping clusters.
    MissingClustersForNonEmptyText,
    /// The shaping output did not include a cluster beginning at source byte 0.
    MissingInitialCluster,
    /// Logical units did not reconstruct the original text exactly.
    LogicalTextMismatch {
        /// Original logical text.
        expected: String,
        /// Text reconstructed from logical PDF units.
        actual: String,
    },
}

impl fmt::Display for CoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSourceRange { start, end } => {
                write!(f, "invalid source range {start}..{end}")
            }
            Self::InvalidUtf8Boundary(offset) => {
                write!(f, "cluster offset {offset} is not a valid UTF-8 boundary")
            }
            Self::MissingClustersForNonEmptyText => {
                write!(f, "non-empty logical text has no shaping clusters")
            }
            Self::MissingInitialCluster => {
                write!(f, "shaping output has no cluster beginning at byte 0")
            }
            Self::LogicalTextMismatch { expected, actual } => {
                write!(
                    f,
                    "logical text mismatch: expected {expected:?}, got {actual:?}"
                )
            }
        }
    }
}

impl std::error::Error for CoreError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn glyph(glyph_id: u32, cluster_start: usize, run_x: i32) -> ShapedGlyph {
        ShapedGlyph {
            glyph_id,
            cluster_start,
            run_x,
            run_y: 0,
            x_offset: 0,
            y_offset: 0,
            x_advance: 500,
            y_advance: 0,
        }
    }

    #[test]
    fn preserves_multi_codepoint_khmer_cluster_once() {
        let text = "កម្ពុជា";
        let second = "ក".len();
        let third = "កម្ពុ".len();
        let glyphs = vec![
            glyph(10, 0, 0),
            glyph(11, second, 500),
            glyph(12, second, 500),
            glyph(13, second, 500),
            glyph(14, third, 1000),
        ];

        let units = logical_units_from_shaped_glyphs(text, FontId(1), &glyphs).unwrap();
        assert_eq!(
            units.iter().map(|u| u.unicode.as_str()).collect::<Vec<_>>(),
            vec!["ក", "ម្ពុ", "ជា"]
        );
        assert_eq!(units[1].glyphs.len(), 3);
        assert_eq!(
            units.iter().map(|u| u.unicode.as_str()).collect::<String>(),
            text
        );
    }

    #[test]
    fn restores_logical_order_from_rtl_visual_glyph_order() {
        let text = "ببب";
        let boundaries: Vec<usize> = text.char_indices().map(|(i, _)| i).collect();
        let glyphs = vec![
            glyph(101, boundaries[2], -1000),
            glyph(104, boundaries[1], -500),
            glyph(102, boundaries[0], 0),
        ];

        let units = logical_units_from_shaped_glyphs(text, FontId(2), &glyphs).unwrap();
        assert_eq!(
            units.iter().map(|u| u.unicode.as_str()).collect::<String>(),
            text
        );
        assert_eq!(units[0].glyphs[0].glyph_id, 102);
        assert_eq!(units[1].glyphs[0].glyph_id, 104);
        assert_eq!(units[2].glyphs[0].glyph_id, 101);
    }

    #[test]
    fn keeps_emoji_zwj_sequence_as_logical_text() {
        let text = "👨‍👩‍👧‍👦";
        let glyphs = vec![glyph(500, 0, 0)];
        let units = logical_units_from_shaped_glyphs(text, FontId(3), &glyphs).unwrap();
        assert_eq!(units.len(), 1);
        assert_eq!(units[0].unicode, text);
    }
}
