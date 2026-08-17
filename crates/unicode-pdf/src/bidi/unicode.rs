//! Pure-Rust Unicode Bidirectional Algorithm backend.

use crate::bidi::{BidiError, BidiParagraph, BidiResolver, BidiRun};
use crate::core::{SourceRange, TextDirection};
use unicode_bidi::{BidiInfo, Level};

/// Pure-Rust `BiDi` resolver backed by `unicode-bidi`.
#[derive(Clone, Copy, Debug, Default)]
pub struct UnicodeBidiResolver;

impl UnicodeBidiResolver {
    /// Creates the resolver.
    ///
    /// # Errors
    ///
    /// This constructor currently cannot fail.
    pub const fn new() -> Result<Self, BidiError> {
        Ok(Self)
    }
}

impl BidiResolver for UnicodeBidiResolver {
    fn resolve(&self, text: &str) -> Result<BidiParagraph, BidiError> {
        if text.is_empty() {
            return Ok(BidiParagraph {
                base_direction: TextDirection::LeftToRight,
                runs: Vec::new(),
            });
        }

        let info = BidiInfo::new(text, None);
        let para = info.paragraphs.first().ok_or(BidiError::BackendFailure)?;
        if para.range.start != 0 || para.range.end != text.len() {
            return Err(BidiError::InvalidRunPartition);
        }

        let mut logical_runs = Vec::<(usize, usize, Level)>::new();
        let mut current_start = 0_usize;
        let mut current_level = info.levels[0];

        for (byte_index, _) in text.char_indices().skip(1) {
            let level = info.levels[byte_index];
            if level != current_level {
                logical_runs.push((current_start, byte_index, current_level));
                current_start = byte_index;
                current_level = level;
            }
        }
        logical_runs.push((current_start, text.len(), current_level));

        let run_levels: Vec<Level> = logical_runs.iter().map(|(_, _, level)| *level).collect();
        let visual_to_logical = BidiInfo::reorder_visual(&run_levels);
        let mut visual_order = vec![0_usize; logical_runs.len()];
        for (visual_index, logical_index) in visual_to_logical.into_iter().enumerate() {
            visual_order[logical_index] = visual_index;
        }

        let runs = logical_runs
            .into_iter()
            .enumerate()
            .map(|(index, (start, end, level))| {
                Ok(BidiRun {
                    source_range: SourceRange::new(start, end)
                        .map_err(|_| BidiError::InvalidRunPartition)?,
                    level: level.number(),
                    direction: if level.is_rtl() {
                        TextDirection::RightToLeft
                    } else {
                        TextDirection::LeftToRight
                    },
                    visual_order: visual_order[index],
                })
            })
            .collect::<Result<Vec<_>, BidiError>>()?;

        let paragraph = BidiParagraph {
            base_direction: if para.level.is_rtl() {
                TextDirection::RightToLeft
            } else {
                TextDirection::LeftToRight
            },
            runs,
        };
        paragraph.validate(text)?;
        Ok(paragraph)
    }
}
