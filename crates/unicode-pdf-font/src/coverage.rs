use super::FontError;

const TAG_CMAP: [u8; 4] = *b"cmap";

/// Parsed Unicode coverage from a TrueType/OpenType `cmap` table.
///
/// Coverage is represented as merged inclusive Unicode scalar ranges. Glyph 0
/// (`.notdef`) is treated as uncovered.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct FontCoverage {
    ranges: Vec<(u32, u32)>,
}

impl FontCoverage {
    /// Parses Unicode coverage from an sfnt font.
    ///
    /// # Errors
    ///
    /// Returns [`FontError`] when the font directory or supported Unicode cmap
    /// subtables are malformed or missing.
    pub fn parse(font: &[u8]) -> Result<Self, FontError> {
        if font.len() < 12 || font.get(..4) == Some(b"ttcf") {
            return Err(FontError::UnsupportedFontCollection);
        }
        let num_tables = usize::from(read_u16(font, 4)?);
        let mut cmap = None;
        for index in 0..num_tables {
            let base = 12 + index * 16;
            let tag = font
                .get(base..base + 4)
                .ok_or(FontError::MalformedTrueType("truncated table directory"))?;
            if tag != TAG_CMAP {
                continue;
            }
            let offset = usize::try_from(read_u32(font, base + 8)?)
                .map_err(|_| FontError::MalformedTrueType("cmap offset too large"))?;
            let length = usize::try_from(read_u32(font, base + 12)?)
                .map_err(|_| FontError::MalformedTrueType("cmap length too large"))?;
            let end = offset.checked_add(length).ok_or(FontError::FontTooLarge)?;
            cmap = Some(
                font.get(offset..end)
                    .ok_or(FontError::MalformedTrueType("cmap extends past font"))?,
            );
            break;
        }
        let cmap = cmap.ok_or(FontError::MissingTrueTypeTable(TAG_CMAP))?;
        if cmap.len() < 4 {
            return Err(FontError::MalformedTrueType("cmap table is too short"));
        }

        let num_subtables = usize::from(read_u16(cmap, 2)?);
        let mut candidates = Vec::new();
        for index in 0..num_subtables {
            let base = 4 + index * 8;
            let platform = read_u16(cmap, base)?;
            let encoding = read_u16(cmap, base + 2)?;
            let offset = usize::try_from(read_u32(cmap, base + 4)?)
                .map_err(|_| FontError::MalformedTrueType("cmap subtable offset too large"))?;
            let format = read_u16(cmap, offset)?;
            let unicode = platform == 0 || (platform == 3 && matches!(encoding, 1 | 10));
            if unicode && matches!(format, 4 | 12) {
                let score = match (format, platform, encoding) {
                    (12, 3, 10) => 400,
                    (12, 0, _) => 350,
                    (4, 3, 1) => 300,
                    (4, 0, _) => 250,
                    _ => 100,
                };
                candidates.push((score, offset, format));
            }
        }
        candidates.sort_by_key(|(score, _, _)| std::cmp::Reverse(*score));
        let (_, offset, format) =
            candidates
                .first()
                .copied()
                .ok_or(FontError::MalformedTrueType(
                    "no supported Unicode cmap subtable",
                ))?;

        let mut ranges = match format {
            12 => parse_format_12(cmap, offset)?,
            4 => parse_format_4(cmap, offset)?,
            _ => unreachable!(),
        };
        ranges.sort_unstable();
        ranges = merge_ranges(ranges);
        Ok(Self { ranges })
    }

    /// Returns whether this font maps the Unicode scalar to a nonzero glyph.
    #[must_use]
    pub fn covers(&self, ch: char) -> bool {
        let code = u32::from(ch);
        let index = self.ranges.partition_point(|(_, end)| *end < code);
        self.ranges
            .get(index)
            .is_some_and(|(start, end)| *start <= code && code <= *end)
    }

    /// Returns the merged inclusive Unicode coverage ranges.
    #[must_use]
    pub fn ranges(&self) -> &[(u32, u32)] {
        &self.ranges
    }
}

fn parse_format_12(cmap: &[u8], offset: usize) -> Result<Vec<(u32, u32)>, FontError> {
    let length = usize::try_from(read_u32(cmap, offset + 4)?)
        .map_err(|_| FontError::MalformedTrueType("format 12 cmap length too large"))?;
    let table = cmap
        .get(offset..offset + length)
        .ok_or(FontError::MalformedTrueType("truncated format 12 cmap"))?;
    let groups = usize::try_from(read_u32(table, 12)?)
        .map_err(|_| FontError::MalformedTrueType("too many cmap groups"))?;
    let mut ranges = Vec::new();
    for index in 0..groups {
        let base = 16 + index * 12;
        let start = read_u32(table, base)?;
        let end = read_u32(table, base + 4)?;
        let start_gid = read_u32(table, base + 8)?;
        if start > end || end > 0x10_FFFF {
            return Err(FontError::MalformedTrueType("invalid format 12 cmap group"));
        }
        // A group with start glyph 0 only leaves its first scalar unmapped; all
        // subsequent scalars map to nonzero sequential glyph IDs.
        let covered_start = if start_gid == 0 {
            start.saturating_add(1)
        } else {
            start
        };
        if covered_start <= end {
            // Exclude surrogate code points even if a malformed font includes them.
            if covered_start <= 0xD7FF {
                ranges.push((covered_start, end.min(0xD7FF)));
            }
            if end >= 0xE000 {
                ranges.push((covered_start.max(0xE000), end));
            }
        }
    }
    Ok(ranges)
}

fn parse_format_4(cmap: &[u8], offset: usize) -> Result<Vec<(u32, u32)>, FontError> {
    let length = usize::from(read_u16(cmap, offset + 2)?);
    let table = cmap
        .get(offset..offset + length)
        .ok_or(FontError::MalformedTrueType("truncated format 4 cmap"))?;
    let seg_count = usize::from(read_u16(table, 6)? / 2);
    if seg_count == 0 {
        return Err(FontError::MalformedTrueType("empty format 4 cmap"));
    }
    let end_codes = 14;
    let start_codes = end_codes + seg_count * 2 + 2;
    let deltas = start_codes + seg_count * 2;
    let range_offsets = deltas + seg_count * 2;
    if range_offsets + seg_count * 2 > table.len() {
        return Err(FontError::MalformedTrueType("truncated format 4 segments"));
    }

    let mut ranges = Vec::new();
    let mut open_start = None;
    let mut previous = 0_u32;
    for code in 0_u32..=0xFFFF {
        if (0xD800..=0xDFFF).contains(&code) {
            if let Some(start) = open_start.take() {
                ranges.push((start, previous));
            }
            continue;
        }
        let gid = format4_glyph_id(
            table,
            seg_count,
            u16::try_from(code)
                .map_err(|_| FontError::MalformedTrueType("format 4 scalar out of range"))?,
        )?;
        if gid != 0 {
            if open_start.is_none() {
                open_start = Some(code);
            }
            previous = code;
        } else if let Some(start) = open_start.take() {
            ranges.push((start, previous));
        }
    }
    if let Some(start) = open_start {
        ranges.push((start, previous));
    }
    Ok(ranges)
}

fn format4_glyph_id(table: &[u8], seg_count: usize, code: u16) -> Result<u16, FontError> {
    let end_codes = 14;
    let start_codes = end_codes + seg_count * 2 + 2;
    let deltas = start_codes + seg_count * 2;
    let range_offsets = deltas + seg_count * 2;

    for segment in 0..seg_count {
        let end = read_u16(table, end_codes + segment * 2)?;
        if code > end {
            continue;
        }
        let start = read_u16(table, start_codes + segment * 2)?;
        if code < start {
            return Ok(0);
        }
        let delta = read_u16(table, deltas + segment * 2)?;
        let range_offset_pos = range_offsets + segment * 2;
        let range_offset = usize::from(read_u16(table, range_offset_pos)?);
        if range_offset == 0 {
            return Ok(code.wrapping_add(delta));
        }
        let glyph_pos = range_offset_pos
            .checked_add(range_offset)
            .and_then(|value| value.checked_add(usize::from(code - start) * 2))
            .ok_or(FontError::MalformedTrueType(
                "format 4 glyph offset overflow",
            ))?;
        let glyph = read_u16(table, glyph_pos)?;
        return Ok(if glyph == 0 {
            0
        } else {
            glyph.wrapping_add(delta)
        });
    }
    Ok(0)
}

fn merge_ranges(ranges: Vec<(u32, u32)>) -> Vec<(u32, u32)> {
    let mut merged: Vec<(u32, u32)> = Vec::new();
    for (start, end) in ranges {
        if start > end {
            continue;
        }
        if let Some((_, previous_end)) = merged.last_mut() {
            if start <= previous_end.saturating_add(1) {
                *previous_end = (*previous_end).max(end);
                continue;
            }
        }
        merged.push((start, end));
    }
    merged
}

fn read_u16(data: &[u8], offset: usize) -> Result<u16, FontError> {
    let bytes: [u8; 2] = data
        .get(offset..offset + 2)
        .ok_or(FontError::MalformedTrueType("truncated cmap u16"))?
        .try_into()
        .map_err(|_| FontError::MalformedTrueType("invalid cmap u16"))?;
    Ok(u16::from_be_bytes(bytes))
}

fn read_u32(data: &[u8], offset: usize) -> Result<u32, FontError> {
    let bytes: [u8; 4] = data
        .get(offset..offset + 4)
        .ok_or(FontError::MalformedTrueType("truncated cmap u32"))?
        .try_into()
        .map_err(|_| FontError::MalformedTrueType("invalid cmap u32"))?;
    Ok(u32::from_be_bytes(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merges_adjacent_ranges() {
        assert_eq!(
            merge_ranges(vec![(1, 2), (3, 5), (8, 9), (9, 12)]),
            vec![(1, 5), (8, 12)]
        );
    }

    #[test]
    fn parses_format_12_unicode_coverage() {
        let mut cmap = Vec::new();
        cmap.extend_from_slice(&0_u16.to_be_bytes());
        cmap.extend_from_slice(&1_u16.to_be_bytes());
        cmap.extend_from_slice(&3_u16.to_be_bytes());
        cmap.extend_from_slice(&10_u16.to_be_bytes());
        cmap.extend_from_slice(&12_u32.to_be_bytes());
        cmap.extend_from_slice(&12_u16.to_be_bytes());
        cmap.extend_from_slice(&0_u16.to_be_bytes());
        cmap.extend_from_slice(&28_u32.to_be_bytes());
        cmap.extend_from_slice(&0_u32.to_be_bytes());
        cmap.extend_from_slice(&1_u32.to_be_bytes());
        cmap.extend_from_slice(&0x1F600_u32.to_be_bytes());
        cmap.extend_from_slice(&0x1F601_u32.to_be_bytes());
        cmap.extend_from_slice(&42_u32.to_be_bytes());

        let mut font = vec![0_u8; 28];
        font[0..4].copy_from_slice(&0x0001_0000_u32.to_be_bytes());
        font[4..6].copy_from_slice(&1_u16.to_be_bytes());
        font[12..16].copy_from_slice(b"cmap");
        font[20..24].copy_from_slice(&28_u32.to_be_bytes());
        font[24..28].copy_from_slice(&u32::try_from(cmap.len()).unwrap().to_be_bytes());
        font.extend_from_slice(&cmap);

        let coverage = FontCoverage::parse(&font).unwrap();
        assert!(coverage.covers('😀'));
        assert!(coverage.covers('😁'));
        assert!(!coverage.covers('A'));
    }
}
