//! Adapter for text that was shaped by an external layout engine.
//!
//! Unlike [`crate::core::logical_units_from_shaped_glyphs`], this adapter
//! accepts complete source ranges instead of cluster start offsets. This is
//! useful for producers such as Typst and Krilla that already retain the
//! original UTF-8 range associated with each shaped glyph.

use std::collections::BTreeMap;
use std::fmt;
use std::ops::Range;

use crate::core::{
    FontId, LogicalPdfUnit, LogicalTextRun, PositionedGlyph, SourceRange, TextDirection,
};

/// A positioned glyph supplied by an external shaping or layout engine.
///
/// `text_range` is a byte range in the complete `text` argument passed to
/// [`logical_units_from_external_glyphs`]. Geometry is expressed in
/// caller-defined font units and is retained without scaling or rounding.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalGlyph {
    /// Font glyph identifier.
    pub glyph_id: u32,
    /// UTF-8 byte range in the original logical string.
    pub text_range: Range<usize>,
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

impl From<&ExternalGlyph> for PositionedGlyph {
    fn from(value: &ExternalGlyph) -> Self {
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

/// Converts externally shaped glyphs into logical PDF units.
///
/// Glyphs with identical source ranges become visual components of the same
/// logical unit. Units are returned in logical source order even when glyphs
/// arrive in visual RTL order. Source intervals that have no glyph are retained
/// as zero-glyph units, so default-ignorable and otherwise glyph-less text is
/// not discarded.
///
/// The input ranges must be non-empty, in bounds, on UTF-8 boundaries, and
/// either identical or non-overlapping. The function rejects partially
/// overlapping ranges because their Unicode ownership is ambiguous.
///
/// # Errors
///
/// Returns [`ExternalTextError`] for malformed or ambiguous source ranges.
pub fn logical_units_from_external_glyphs(
    text: &str,
    font_id: FontId,
    glyphs: &[ExternalGlyph],
) -> Result<LogicalTextRun, ExternalTextError> {
    let mut glyphs_by_range: BTreeMap<(usize, usize), Vec<PositionedGlyph>> = BTreeMap::new();

    for (glyph_index, glyph) in glyphs.iter().enumerate() {
        let start = glyph.text_range.start;
        let end = glyph.text_range.end;
        if start > end || end > text.len() {
            return Err(ExternalTextError::InvalidSourceRange {
                glyph_index,
                start,
                end,
                text_len: text.len(),
            });
        }
        if start == end {
            return Err(ExternalTextError::EmptySourceRange {
                glyph_index,
                offset: start,
            });
        }
        for offset in [start, end] {
            if !text.is_char_boundary(offset) {
                return Err(ExternalTextError::InvalidUtf8Boundary {
                    glyph_index,
                    offset,
                });
            }
        }

        glyphs_by_range
            .entry((start, end))
            .or_default()
            .push(PositionedGlyph::from(glyph));
    }

    let mut units = Vec::with_capacity(glyphs_by_range.len().saturating_add(1));
    let mut cursor = 0;
    let mut previous_range = None;

    for ((start, end), positioned) in glyphs_by_range {
        if start < cursor {
            let previous = previous_range.unwrap_or(0..cursor);
            return Err(ExternalTextError::OverlappingSourceRanges {
                previous,
                next: start..end,
            });
        }

        if cursor < start {
            units.push(unit_from_range(text, cursor..start, font_id, Vec::new()));
        }

        units.push(unit_from_range(text, start..end, font_id, positioned));
        cursor = end;
        previous_range = Some(start..end);
    }

    if cursor < text.len() {
        units.push(unit_from_range(
            text,
            cursor..text.len(),
            font_id,
            Vec::new(),
        ));
    }

    Ok(LogicalTextRun {
        original_text: text.to_owned(),
        direction: TextDirection::Auto,
        units,
    })
}

fn unit_from_range(
    text: &str,
    range: Range<usize>,
    font_id: FontId,
    glyphs: Vec<PositionedGlyph>,
) -> LogicalPdfUnit {
    LogicalPdfUnit {
        unicode: text[range.clone()].to_owned(),
        source_range: Some(SourceRange(range)),
        font_id,
        glyphs,
    }
}

/// Errors produced while adapting externally shaped glyphs.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExternalTextError {
    /// A glyph range is reversed or extends beyond the source string.
    InvalidSourceRange {
        /// Index of the malformed glyph.
        glyph_index: usize,
        /// First byte offset of the range.
        start: usize,
        /// Exclusive final byte offset of the range.
        end: usize,
        /// Length of the source string in bytes.
        text_len: usize,
    },
    /// A glyph has no source text range.
    EmptySourceRange {
        /// Index of the malformed glyph.
        glyph_index: usize,
        /// Empty range position.
        offset: usize,
    },
    /// A range endpoint is not a UTF-8 character boundary.
    InvalidUtf8Boundary {
        /// Index of the malformed glyph.
        glyph_index: usize,
        /// Invalid byte offset.
        offset: usize,
    },
    /// Two different source ranges overlap.
    OverlappingSourceRanges {
        /// Earlier range in logical source order.
        previous: Range<usize>,
        /// Later overlapping range.
        next: Range<usize>,
    },
}

impl fmt::Display for ExternalTextError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSourceRange {
                glyph_index,
                start,
                end,
                text_len,
            } => write!(
                f,
                "glyph {glyph_index} has invalid source range {start}..{end} for text length {text_len}"
            ),
            Self::EmptySourceRange {
                glyph_index,
                offset,
            } => write!(
                f,
                "glyph {glyph_index} has an empty source range at byte {offset}"
            ),
            Self::InvalidUtf8Boundary {
                glyph_index,
                offset,
            } => write!(
                f,
                "glyph {glyph_index} source offset {offset} is not a UTF-8 boundary"
            ),
            Self::OverlappingSourceRanges { previous, next } => write!(
                f,
                "external glyph source ranges {previous:?} and {next:?} overlap"
            ),
        }
    }
}

impl std::error::Error for ExternalTextError {}

#[cfg(test)]
mod tests {
    use super::*;

    fn glyph(glyph_id: u32, text_range: Range<usize>, run_x: i32) -> ExternalGlyph {
        ExternalGlyph {
            glyph_id,
            text_range,
            run_x,
            run_y: 0,
            x_offset: 0,
            y_offset: 0,
            x_advance: 500,
            y_advance: 0,
        }
    }

    #[test]
    fn groups_equal_ranges_into_one_semantic_unit() {
        let text = "កម្ពុជា";
        let first_end = "ក".len();
        let second_end = "កម្ពុ".len();
        let glyphs = vec![
            glyph(10, 0..first_end, 0),
            glyph(11, first_end..second_end, 500),
            glyph(12, first_end..second_end, 500),
            glyph(13, first_end..second_end, 500),
            glyph(14, second_end..text.len(), 1000),
        ];

        let run = logical_units_from_external_glyphs(text, FontId(1), &glyphs).unwrap();

        run.validate_round_trip().unwrap();
        assert_eq!(run.units.len(), 3);
        assert_eq!(run.units[1].unicode, "ម្ពុ");
        assert_eq!(run.units[1].glyphs.len(), 3);
    }

    #[test]
    fn restores_logical_order_without_changing_visual_positions() {
        let text = "ببب";
        let starts: Vec<usize> = text.char_indices().map(|(index, _)| index).collect();
        let glyphs = vec![
            glyph(30, starts[2]..text.len(), 0),
            glyph(20, starts[1]..starts[2], 500),
            glyph(10, starts[0]..starts[1], 1000),
        ];

        let run = logical_units_from_external_glyphs(text, FontId(2), &glyphs).unwrap();

        run.validate_round_trip().unwrap();
        assert_eq!(
            run.units
                .iter()
                .map(|unit| unit.glyphs[0].glyph_id)
                .collect::<Vec<_>>(),
            vec![10, 20, 30]
        );
        assert_eq!(run.units[0].glyphs[0].run_x, 1000);
        assert_eq!(run.units[2].glyphs[0].run_x, 0);
    }

    #[test]
    fn preserves_source_intervals_that_have_no_glyphs() {
        let text = "a\u{200b}b";
        let glyphs = vec![glyph(1, 0..1, 0), glyph(2, 4..5, 500)];

        let run = logical_units_from_external_glyphs(text, FontId(3), &glyphs).unwrap();

        run.validate_round_trip().unwrap();
        assert_eq!(run.units.len(), 3);
        assert_eq!(run.units[1].unicode, "\u{200b}");
        assert!(run.units[1].glyphs.is_empty());
    }

    #[test]
    fn preserves_all_text_when_no_glyphs_are_supplied() {
        let run = logical_units_from_external_glyphs("\u{200b}", FontId(4), &[]).unwrap();

        run.validate_round_trip().unwrap();
        assert_eq!(run.units.len(), 1);
        assert!(run.units[0].glyphs.is_empty());
    }

    #[test]
    fn rejects_partially_overlapping_ranges() {
        let glyphs = vec![glyph(1, 0..2, 0), glyph(2, 1..3, 500)];

        let error = logical_units_from_external_glyphs("abc", FontId(5), &glyphs).unwrap_err();

        assert_eq!(
            error,
            ExternalTextError::OverlappingSourceRanges {
                previous: 0..2,
                next: 1..3,
            }
        );
    }

    #[test]
    fn rejects_non_utf8_range_boundaries() {
        let glyphs = vec![glyph(1, 0..1, 0)];

        let error = logical_units_from_external_glyphs("ក", FontId(6), &glyphs).unwrap_err();

        assert_eq!(
            error,
            ExternalTextError::InvalidUtf8Boundary {
                glyph_index: 0,
                offset: 1,
            }
        );
    }
}
