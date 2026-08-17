//! CID planning primitives for `unicode-pdf`.
//!
//! The production implementation will also synthesize and subset TrueType
//! composite glyphs. This crate already defines the key needed to distinguish
//! contextually different visual forms that share the same logical Unicode.

use std::collections::HashMap;
use std::fmt;

use unicode_pdf_core::{FontId, LogicalPdfUnit};

mod coverage;
mod truetype;

pub use coverage::FontCoverage;
pub use truetype::{synthesize_truetype_composites, SynthesizedTrueTypeFont, SyntheticGlyphRecord};

/// PDF character identifier. CID 0 is reserved.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Cid(pub u16);

/// One glyph component normalized relative to a logical unit's visual origin.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct VisualComponentKey {
    /// Font glyph identifier.
    pub glyph_id: u32,
    /// Relative X position.
    pub x: i32,
    /// Relative Y position.
    pub y: i32,
    /// X advance produced by shaping.
    pub x_advance: i32,
    /// Y advance produced by shaping.
    pub y_advance: i32,
}

/// Identity of a reusable visual logical unit.
///
/// Unicode is intentionally part of this key, but is not the whole key. This
/// allows two Arabic contextual forms to map to the same Unicode while using
/// different CIDs and visual glyph components.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct VisualUnitKey {
    /// Exact logical Unicode represented by the unit.
    pub unicode: String,
    /// Font or font instance used by the unit.
    pub font_id: FontId,
    /// Positive horizontal advance of the logical unit in font units.
    pub advance_width: i32,
    /// Normalized visual components.
    pub components: Vec<VisualComponentKey>,
}

impl VisualUnitKey {
    /// Builds a reusable visual key from a logical PDF unit.
    #[must_use]
    pub fn from_unit(unit: &LogicalPdfUnit) -> Self {
        let origin_x = unit
            .glyphs
            .iter()
            .flat_map(|glyph| [glyph.run_x, glyph.run_x.saturating_add(glyph.x_advance)])
            .min()
            .unwrap_or(0);
        let visual_end_x = unit
            .glyphs
            .iter()
            .flat_map(|glyph| [glyph.run_x, glyph.run_x.saturating_add(glyph.x_advance)])
            .max()
            .unwrap_or(origin_x);
        let advance_width = visual_end_x.saturating_sub(origin_x);

        let components = unit
            .glyphs
            .iter()
            .map(|glyph| VisualComponentKey {
                glyph_id: glyph.glyph_id,
                x: glyph.run_x + glyph.x_offset - origin_x,
                // Y remains relative to the shaping baseline. Normalizing Y by
                // the cluster's minimum would move marks vertically.
                y: glyph.run_y + glyph.y_offset,
                x_advance: glyph.x_advance,
                y_advance: glyph.y_advance,
            })
            .collect();

        Self {
            unicode: unit.unicode.clone(),
            font_id: unit.font_id,
            advance_width,
            components,
        }
    }
}

/// A CID and the visual/logical key it represents.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CidEntry {
    /// Allocated PDF CID.
    pub cid: Cid,
    /// Reusable logical/visual key.
    pub key: VisualUnitKey,
}

/// Allocates stable CIDs for reusable visual units.
#[derive(Debug, Default)]
pub struct CidAllocator {
    by_key: HashMap<VisualUnitKey, Cid>,
    entries: Vec<CidEntry>,
}

impl CidAllocator {
    /// Creates an empty allocator.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns an existing CID for an identical visual unit or allocates a new one.
    ///
    /// # Errors
    ///
    /// Returns [`FontError::CidSpaceExhausted`] when the current 16-bit CID
    /// subset has no remaining nonzero CIDs.
    pub fn get_or_allocate(&mut self, unit: &LogicalPdfUnit) -> Result<Cid, FontError> {
        let key = VisualUnitKey::from_unit(unit);
        if let Some(cid) = self.by_key.get(&key).copied() {
            return Ok(cid);
        }

        let next = self.entries.len() + 1;
        let raw = u16::try_from(next).map_err(|_| FontError::CidSpaceExhausted)?;
        if raw == 0 {
            return Err(FontError::CidSpaceExhausted);
        }

        let cid = Cid(raw);
        self.by_key.insert(key.clone(), cid);
        self.entries.push(CidEntry { cid, key });
        Ok(cid)
    }

    /// Returns all allocated entries in CID allocation order.
    #[must_use]
    pub fn entries(&self) -> &[CidEntry] {
        &self.entries
    }
}

/// Errors produced by CID/font planning.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FontError {
    /// All nonzero 16-bit CIDs in the current font subset were consumed.
    CidSpaceExhausted,
    /// The source TrueType font is structurally malformed.
    MalformedTrueType(&'static str),
    /// A required TrueType table is missing.
    MissingTrueTypeTable([u8; 4]),
    /// TrueType collections are not yet supported by the synthesizer.
    UnsupportedFontCollection,
    /// A shaped glyph ID does not exist in the source TrueType font.
    GlyphIdOutOfRange(u32),
    /// A composite component coordinate does not fit the TrueType i16 format.
    CoordinateOutOfRange(i32),
    /// A synthetic horizontal advance does not fit the TrueType u16 format.
    AdvanceOutOfRange(i32),
    /// Appending synthetic glyphs would exceed the TrueType glyph limit.
    TooManyGlyphs,
    /// The rebuilt sfnt exceeds 32-bit table offsets.
    FontTooLarge,
    /// The sfnt contains more tables than its 16-bit table count can encode.
    TooManyTables,
}

impl fmt::Display for FontError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::CidSpaceExhausted => write!(f, "CID space exhausted; start another font subset"),
            Self::MalformedTrueType(message) => write!(f, "malformed TrueType font: {message}"),
            Self::MissingTrueTypeTable(tag) => {
                write!(
                    f,
                    "required TrueType table {:?} is missing",
                    String::from_utf8_lossy(tag)
                )
            }
            Self::UnsupportedFontCollection => {
                write!(f, "TrueType collections are not supported yet")
            }
            Self::GlyphIdOutOfRange(glyph_id) => {
                write!(f, "glyph ID {glyph_id} is outside the source font")
            }
            Self::CoordinateOutOfRange(value) => {
                write!(f, "composite coordinate {value} does not fit i16")
            }
            Self::AdvanceOutOfRange(value) => {
                write!(f, "synthetic advance {value} does not fit u16")
            }
            Self::TooManyGlyphs => write!(f, "synthetic font would exceed 65,535 glyphs"),
            Self::FontTooLarge => write!(f, "font exceeds the sfnt 32-bit offset limit"),
            Self::TooManyTables => write!(f, "font contains too many sfnt tables"),
        }
    }
}

impl std::error::Error for FontError {}

#[cfg(test)]
mod tests {
    use unicode_pdf_core::{FontId, LogicalPdfUnit, PositionedGlyph};

    use super::*;

    fn arabic_unit(glyph_id: u32) -> LogicalPdfUnit {
        LogicalPdfUnit {
            unicode: "ب".to_owned(),
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
    fn reuses_identical_visual_units() {
        let unit = arabic_unit(100);
        let mut allocator = CidAllocator::new();
        let a = allocator.get_or_allocate(&unit).unwrap();
        let b = allocator.get_or_allocate(&unit).unwrap();
        assert_eq!(a, b);
        assert_eq!(allocator.entries().len(), 1);
    }

    #[test]
    fn separates_contextually_different_shapes_with_same_unicode() {
        let mut allocator = CidAllocator::new();
        let initial = allocator.get_or_allocate(&arabic_unit(102)).unwrap();
        let medial = allocator.get_or_allocate(&arabic_unit(104)).unwrap();
        let final_form = allocator.get_or_allocate(&arabic_unit(101)).unwrap();

        assert_ne!(initial, medial);
        assert_ne!(medial, final_form);
        assert_eq!(allocator.entries().len(), 3);
        assert!(allocator
            .entries()
            .iter()
            .all(|entry| entry.key.unicode == "ب"));
    }
}
