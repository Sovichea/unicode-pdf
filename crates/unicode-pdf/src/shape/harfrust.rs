//! Pure-Rust HarfRust shaping backend.

use crate::core::{logical_units_from_shaped_glyphs, LogicalTextRun, ShapedGlyph, TextDirection};
use crate::shape::{ShapeError, ShapeOptions, ShapeOutput, TextShaper};
use harfrust::{
    Direction, FontRef, ShapeOptions as HarfRustShapeOptions, ShaperData, UnicodeBuffer,
};

/// Pure-Rust text shaper backed by HarfRust.
#[derive(Clone, Copy, Debug, Default)]
pub struct HarfRustShaper;

impl HarfRustShaper {
    /// Creates a HarfRust shaper.
    ///
    /// This mirrors the fallible constructor of the optional system-HarfBuzz
    /// backend, although HarfRust itself requires no runtime library loading.
    ///
    /// # Errors
    ///
    /// This constructor currently cannot fail.
    pub const fn new() -> Result<Self, ShapeError> {
        Ok(Self)
    }
}

impl TextShaper for HarfRustShaper {
    fn shape(
        &self,
        font_data: &[u8],
        text: &str,
        options: ShapeOptions,
    ) -> Result<ShapeOutput, ShapeError> {
        let font = FontRef::from_index(font_data, options.face_index)
            .map_err(|_| ShapeError::InvalidFont)?;
        let shaper_data = ShaperData::new(&font);
        let shaper = shaper_data.shaper(&font).build();
        let units_per_em =
            u32::try_from(shaper.units_per_em()).map_err(|_| ShapeError::InvalidFont)?;
        if units_per_em == 0 {
            return Err(ShapeError::InvalidFont);
        }

        if text.is_empty() {
            let direction = match options.direction {
                TextDirection::RightToLeft => TextDirection::RightToLeft,
                TextDirection::LeftToRight | TextDirection::Auto => TextDirection::LeftToRight,
            };
            return Ok(ShapeOutput {
                units_per_em,
                run: LogicalTextRun {
                    original_text: String::new(),
                    direction,
                    units: Vec::new(),
                },
            });
        }

        let mut buffer = UnicodeBuffer::new();
        buffer.push_str(text);
        match options.direction {
            TextDirection::LeftToRight => buffer.set_direction(Direction::LeftToRight),
            TextDirection::RightToLeft => buffer.set_direction(Direction::RightToLeft),
            TextDirection::Auto => {}
        }
        buffer.guess_segment_properties();

        let direction = match buffer.direction() {
            Direction::LeftToRight => TextDirection::LeftToRight,
            Direction::RightToLeft => TextDirection::RightToLeft,
            other => return Err(ShapeError::UnsupportedDirection(other as u32)),
        };

        let glyph_buffer = shaper.shape(buffer, HarfRustShapeOptions::new());
        let infos = glyph_buffer.glyph_infos();
        let positions = glyph_buffer.glyph_positions();
        if infos.len() != positions.len() {
            return Err(ShapeError::InvalidBackendOutput);
        }

        let mut glyphs = Vec::with_capacity(infos.len());
        let mut pen_x = 0_i64;
        let mut pen_y = 0_i64;
        for (info, position) in infos.iter().zip(positions) {
            let run_x = i32::try_from(pen_x).map_err(|_| ShapeError::CoordinateOverflow)?;
            let run_y = i32::try_from(pen_y).map_err(|_| ShapeError::CoordinateOverflow)?;
            let cluster_start =
                usize::try_from(info.cluster).map_err(|_| ShapeError::InputTooLarge)?;
            glyphs.push(ShapedGlyph {
                glyph_id: info.glyph_id,
                cluster_start,
                run_x,
                run_y,
                x_offset: position.x_offset,
                y_offset: position.y_offset,
                x_advance: position.x_advance,
                y_advance: position.y_advance,
            });
            pen_x += i64::from(position.x_advance);
            pen_y += i64::from(position.y_advance);
        }

        let units = logical_units_from_shaped_glyphs(text, options.font_id, &glyphs)
            .map_err(|error| ShapeError::LogicalModel(error.to_string()))?;
        let run = LogicalTextRun {
            original_text: text.to_owned(),
            direction,
            units,
        };
        run.validate_round_trip()
            .map_err(|error| ShapeError::LogicalModel(error.to_string()))?;

        Ok(ShapeOutput { units_per_em, run })
    }
}
