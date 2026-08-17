use std::collections::{BTreeMap, HashMap, HashSet};

use super::{Cid, CidEntry, FontError, VisualUnitKey};

const TAG_HEAD: [u8; 4] = *b"head";
const TAG_HHEA: [u8; 4] = *b"hhea";
const TAG_HMTX: [u8; 4] = *b"hmtx";
const TAG_LOCA: [u8; 4] = *b"loca";
const TAG_GLYF: [u8; 4] = *b"glyf";
const TAG_MAXP: [u8; 4] = *b"maxp";
const TAG_POST: [u8; 4] = *b"post";

const COMPOSITE_ARG_WORDS: u16 = 0x0001;
const COMPOSITE_ARGS_XY: u16 = 0x0002;
const COMPOSITE_ROUND_XY: u16 = 0x0004;
const COMPOSITE_HAVE_SCALE: u16 = 0x0008;
const COMPOSITE_MORE: u16 = 0x0020;
const COMPOSITE_HAVE_XY_SCALE: u16 = 0x0040;
const COMPOSITE_HAVE_2X2: u16 = 0x0080;
const COMPOSITE_HAVE_INSTRUCTIONS: u16 = 0x0100;

const CHECKSUM_MAGIC: u32 = 0xB1B0_AFBA;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct BBox {
    x_min: i16,
    y_min: i16,
    x_max: i16,
    y_max: i16,
}

impl BBox {
    fn translated(self, x: i16, y: i16) -> Result<Self, FontError> {
        Ok(Self {
            x_min: checked_i16(i32::from(self.x_min) + i32::from(x))?,
            y_min: checked_i16(i32::from(self.y_min) + i32::from(y))?,
            x_max: checked_i16(i32::from(self.x_max) + i32::from(x))?,
            y_max: checked_i16(i32::from(self.y_max) + i32::from(y))?,
        })
    }

    fn union(self, other: Self) -> Self {
        Self {
            x_min: self.x_min.min(other.x_min),
            y_min: self.y_min.min(other.y_min),
            x_max: self.x_max.max(other.x_max),
            y_max: self.y_max.max(other.y_max),
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct GlyphStats {
    points: u32,
    contours: u32,
    depth: u16,
}

#[derive(Clone, Debug)]
struct Table {
    tag: [u8; 4],
    data: Vec<u8>,
}

#[derive(Clone, Debug)]
struct ParsedFont {
    scaler_type: u32,
    tables: BTreeMap<[u8; 4], Vec<u8>>,
}

/// One synthetic glyph created for a reusable PDF CID.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SyntheticGlyphRecord {
    /// PDF CID associated with the synthetic glyph.
    pub cid: Cid,
    /// Glyph identifier in the synthesized TrueType font.
    pub glyph_id: u16,
    /// Exact Unicode mapped from the CID.
    pub unicode: String,
    /// Positive horizontal advance in font units.
    pub advance_width: u16,
    /// Number of original-font glyph components in this synthetic glyph.
    pub component_count: usize,
}

/// Result of appending logical composite glyphs to a TrueType font.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SynthesizedTrueTypeFont {
    /// Complete standalone TrueType font bytes.
    pub bytes: Vec<u8>,
    /// Number of glyphs in the source font.
    pub base_glyph_count: u16,
    /// Synthetic glyph records in CID allocation order.
    pub synthetic_glyphs: Vec<SyntheticGlyphRecord>,
}

/// Appends one TrueType composite glyph per unique CID entry.
///
/// The source font is retained intact except for tables that must be rewritten
/// to account for the appended glyphs. OpenType shaping tables are removed from
/// the synthesized font because the PDF renderer addresses the final glyph IDs
/// directly through `/CIDToGIDMap`; shaping has already happened upstream.
///
/// # Errors
///
/// Returns [`FontError`] for malformed/unsupported fonts, out-of-range shaping
/// coordinates, invalid source glyph IDs, or the 65,535-glyph TrueType limit.
#[allow(clippy::too_many_lines)]
pub fn synthesize_truetype_composites(
    source_font: &[u8],
    entries: &[CidEntry],
) -> Result<SynthesizedTrueTypeFont, FontError> {
    let mut parsed = ParsedFont::parse(source_font)?;

    let maxp = required_table(&parsed.tables, TAG_MAXP)?;
    if maxp.len() < 6 {
        return Err(FontError::MalformedTrueType("maxp table is too short"));
    }
    let base_glyph_count = read_u16(maxp, 4)?;
    let new_glyph_count = usize::from(base_glyph_count)
        .checked_add(entries.len())
        .ok_or(FontError::TooManyGlyphs)?;
    if new_glyph_count > usize::from(u16::MAX) {
        return Err(FontError::TooManyGlyphs);
    }
    let new_glyph_count_u16 =
        u16::try_from(new_glyph_count).map_err(|_| FontError::TooManyGlyphs)?;

    let head = required_table(&parsed.tables, TAG_HEAD)?;
    if head.len() < 54 {
        return Err(FontError::MalformedTrueType("head table is too short"));
    }
    let loca_format = read_i16(head, 50)?;
    if !matches!(loca_format, 0 | 1) {
        return Err(FontError::MalformedTrueType("unsupported loca format"));
    }

    let original_glyf = required_table(&parsed.tables, TAG_GLYF)?.clone();
    let original_loca = required_table(&parsed.tables, TAG_LOCA)?;
    let loca = parse_loca(
        original_loca,
        base_glyph_count,
        loca_format,
        original_glyf.len(),
    )?;
    let original_metrics = parse_hmetrics(&parsed.tables, base_glyph_count)?;

    let base_glyf_end = usize::try_from(*loca.last().ok_or(FontError::MalformedTrueType(
        "loca table has no terminal offset",
    ))?)
    .map_err(|_| FontError::MalformedTrueType("glyf offset does not fit usize"))?;
    let mut glyf = original_glyf
        .get(..base_glyf_end)
        .ok_or(FontError::MalformedTrueType("loca exceeds glyf table"))?
        .to_vec();
    let mut new_loca = loca;
    let mut new_metrics = original_metrics;
    let mut synthetic_glyphs = Vec::with_capacity(entries.len());

    let mut stats_cache = HashMap::new();
    let mut visiting = HashSet::new();
    let mut max_synthetic_points = 0_u32;
    let mut max_synthetic_contours = 0_u32;
    let mut max_synthetic_components = 0_u16;
    let mut max_synthetic_depth = 0_u16;

    for (index, entry) in entries.iter().enumerate() {
        let gid = usize::from(base_glyph_count) + index;
        let gid = u16::try_from(gid).map_err(|_| FontError::TooManyGlyphs)?;
        let (glyph_bytes, bbox, advance_width, stats) = build_composite_glyph(
            &entry.key,
            base_glyph_count,
            &original_glyf,
            &new_loca[..=usize::from(base_glyph_count)],
            &mut stats_cache,
            &mut visiting,
        )?;

        glyf.extend_from_slice(&glyph_bytes);
        while glyf.len() % 4 != 0 {
            glyf.push(0);
        }
        new_loca.push(u32::try_from(glyf.len()).map_err(|_| FontError::FontTooLarge)?);
        new_metrics.push((advance_width, bbox.x_min));
        synthetic_glyphs.push(SyntheticGlyphRecord {
            cid: entry.cid,
            glyph_id: gid,
            unicode: entry.key.unicode.clone(),
            advance_width,
            component_count: entry.key.components.len(),
        });

        max_synthetic_points = max_synthetic_points.max(stats.points);
        max_synthetic_contours = max_synthetic_contours.max(stats.contours);
        max_synthetic_components = max_synthetic_components
            .max(u16::try_from(entry.key.components.len()).unwrap_or(u16::MAX));
        max_synthetic_depth = max_synthetic_depth.max(stats.depth);
    }

    let mut head = required_table(&parsed.tables, TAG_HEAD)?.clone();
    write_i16(&mut head, 50, 1)?;
    write_u32(&mut head, 8, 0)?;
    parsed.tables.insert(TAG_HEAD, head);

    let mut maxp = required_table(&parsed.tables, TAG_MAXP)?.clone();
    write_u16(&mut maxp, 4, new_glyph_count_u16)?;
    if maxp.len() >= 32 {
        let old_composite_points = read_u16(&maxp, 10)?;
        let old_composite_contours = read_u16(&maxp, 12)?;
        let old_component_elements = read_u16(&maxp, 28)?;
        let old_component_depth = read_u16(&maxp, 30)?;
        write_u16(
            &mut maxp,
            10,
            old_composite_points.max(saturating_u16(max_synthetic_points)),
        )?;
        write_u16(
            &mut maxp,
            12,
            old_composite_contours.max(saturating_u16(max_synthetic_contours)),
        )?;
        write_u16(
            &mut maxp,
            28,
            old_component_elements.max(max_synthetic_components),
        )?;
        write_u16(&mut maxp, 30, old_component_depth.max(max_synthetic_depth))?;
    }
    parsed.tables.insert(TAG_MAXP, maxp);

    let mut hhea = required_table(&parsed.tables, TAG_HHEA)?.clone();
    if hhea.len() < 36 {
        return Err(FontError::MalformedTrueType("hhea table is too short"));
    }
    write_u16(&mut hhea, 34, new_glyph_count_u16)?;
    parsed.tables.insert(TAG_HHEA, hhea);
    parsed
        .tables
        .insert(TAG_HMTX, serialize_hmetrics(&new_metrics));
    parsed.tables.insert(TAG_GLYF, glyf);
    parsed
        .tables
        .insert(TAG_LOCA, serialize_long_loca(&new_loca));

    if let Some(post) = parsed.tables.get_mut(&TAG_POST) {
        if post.len() >= 32 {
            post.truncate(32);
            write_u32(post, 0, 0x0003_0000)?;
        }
    }

    for tag in [*b"GSUB", *b"GPOS", *b"GDEF", *b"DSIG"] {
        parsed.tables.remove(&tag);
    }

    let bytes = parsed.build()?;
    Ok(SynthesizedTrueTypeFont {
        bytes,
        base_glyph_count,
        synthetic_glyphs,
    })
}

fn build_composite_glyph(
    key: &VisualUnitKey,
    base_glyph_count: u16,
    glyf: &[u8],
    loca: &[u32],
    stats_cache: &mut HashMap<u16, GlyphStats>,
    visiting: &mut HashSet<u16>,
) -> Result<(Vec<u8>, BBox, u16, GlyphStats), FontError> {
    let advance_width = u16::try_from(key.advance_width)
        .map_err(|_| FontError::AdvanceOutOfRange(key.advance_width))?;

    if key.components.is_empty() {
        return Ok((
            Vec::new(),
            BBox::default(),
            advance_width,
            GlyphStats::default(),
        ));
    }

    let mut bbox = None;
    let mut points = 0_u32;
    let mut contours = 0_u32;
    let mut depth = 1_u16;
    let mut component_records = Vec::with_capacity(key.components.len());

    for component in &key.components {
        let gid = u16::try_from(component.glyph_id)
            .map_err(|_| FontError::GlyphIdOutOfRange(component.glyph_id))?;
        if gid >= base_glyph_count {
            return Err(FontError::GlyphIdOutOfRange(component.glyph_id));
        }
        let x = checked_i16(component.x)?;
        let y = checked_i16(component.y)?;
        let base_bbox = glyph_bbox(gid, glyf, loca)?.translated(x, y)?;
        bbox = Some(bbox.map_or(base_bbox, |current: BBox| current.union(base_bbox)));

        let child_stats = glyph_stats(gid, glyf, loca, stats_cache, visiting)?;
        points = points.saturating_add(child_stats.points);
        contours = contours.saturating_add(child_stats.contours);
        depth = depth.max(child_stats.depth.saturating_add(1));
        component_records.push((gid, x, y));
    }

    let bbox = bbox.unwrap_or_default();
    let mut bytes = Vec::with_capacity(10 + component_records.len() * 8);
    push_i16(&mut bytes, -1);
    push_i16(&mut bytes, bbox.x_min);
    push_i16(&mut bytes, bbox.y_min);
    push_i16(&mut bytes, bbox.x_max);
    push_i16(&mut bytes, bbox.y_max);

    let last = component_records.len().saturating_sub(1);
    for (index, (gid, x, y)) in component_records.into_iter().enumerate() {
        let mut flags = COMPOSITE_ARG_WORDS | COMPOSITE_ARGS_XY | COMPOSITE_ROUND_XY;
        if index != last {
            flags |= COMPOSITE_MORE;
        }
        push_u16(&mut bytes, flags);
        push_u16(&mut bytes, gid);
        push_i16(&mut bytes, x);
        push_i16(&mut bytes, y);
    }

    Ok((
        bytes,
        bbox,
        advance_width,
        GlyphStats {
            points,
            contours,
            depth,
        },
    ))
}

fn glyph_bbox(gid: u16, glyf: &[u8], loca: &[u32]) -> Result<BBox, FontError> {
    let data = glyph_data(gid, glyf, loca)?;
    if data.is_empty() {
        return Ok(BBox::default());
    }
    if data.len() < 10 {
        return Err(FontError::MalformedTrueType("glyf record is too short"));
    }
    Ok(BBox {
        x_min: read_i16(data, 2)?,
        y_min: read_i16(data, 4)?,
        x_max: read_i16(data, 6)?,
        y_max: read_i16(data, 8)?,
    })
}

fn glyph_data<'a>(gid: u16, glyf: &'a [u8], loca: &[u32]) -> Result<&'a [u8], FontError> {
    let index = usize::from(gid);
    let start = usize::try_from(
        *loca
            .get(index)
            .ok_or(FontError::MalformedTrueType("glyph ID exceeds loca"))?,
    )
    .map_err(|_| FontError::MalformedTrueType("glyf offset does not fit usize"))?;
    let end = usize::try_from(*loca.get(index + 1).ok_or(FontError::MalformedTrueType(
        "glyph ID has no terminal loca offset",
    ))?)
    .map_err(|_| FontError::MalformedTrueType("glyf offset does not fit usize"))?;
    glyf.get(start..end)
        .ok_or(FontError::MalformedTrueType("loca slice exceeds glyf"))
}

fn glyph_stats(
    gid: u16,
    glyf: &[u8],
    loca: &[u32],
    cache: &mut HashMap<u16, GlyphStats>,
    visiting: &mut HashSet<u16>,
) -> Result<GlyphStats, FontError> {
    if let Some(stats) = cache.get(&gid).copied() {
        return Ok(stats);
    }
    if !visiting.insert(gid) {
        return Err(FontError::MalformedTrueType("recursive composite glyph"));
    }

    let data = glyph_data(gid, glyf, loca)?;
    let stats = if data.is_empty() {
        GlyphStats::default()
    } else if data.len() < 10 {
        return Err(FontError::MalformedTrueType("glyf record is too short"));
    } else {
        let contours = read_i16(data, 0)?;
        if contours >= 0 {
            let contours_u16 = u16::try_from(contours)
                .map_err(|_| FontError::MalformedTrueType("negative simple contour count"))?;
            if contours_u16 == 0 {
                GlyphStats::default()
            } else {
                let last_endpoint_offset = 10 + (usize::from(contours_u16) - 1) * 2;
                let last_endpoint = read_u16(data, last_endpoint_offset)?;
                GlyphStats {
                    points: u32::from(last_endpoint) + 1,
                    contours: u32::from(contours_u16),
                    depth: 0,
                }
            }
        } else {
            composite_stats(data, glyf, loca, cache, visiting)?
        }
    };

    visiting.remove(&gid);
    cache.insert(gid, stats);
    Ok(stats)
}

fn composite_stats(
    data: &[u8],
    glyf: &[u8],
    loca: &[u32],
    cache: &mut HashMap<u16, GlyphStats>,
    visiting: &mut HashSet<u16>,
) -> Result<GlyphStats, FontError> {
    let mut offset = 10_usize;
    let mut points = 0_u32;
    let mut contours = 0_u32;
    let mut depth = 1_u16;

    loop {
        let flags = read_u16(data, offset)?;
        let child_gid = read_u16(data, offset + 2)?;
        offset += 4;
        offset += if flags & COMPOSITE_ARG_WORDS != 0 {
            4
        } else {
            2
        };
        if flags & COMPOSITE_HAVE_SCALE != 0 {
            offset += 2;
        } else if flags & COMPOSITE_HAVE_XY_SCALE != 0 {
            offset += 4;
        } else if flags & COMPOSITE_HAVE_2X2 != 0 {
            offset += 8;
        }
        if offset > data.len() {
            return Err(FontError::MalformedTrueType("truncated composite glyph"));
        }

        let child = glyph_stats(child_gid, glyf, loca, cache, visiting)?;
        points = points.saturating_add(child.points);
        contours = contours.saturating_add(child.contours);
        depth = depth.max(child.depth.saturating_add(1));

        if flags & COMPOSITE_MORE == 0 {
            if flags & COMPOSITE_HAVE_INSTRUCTIONS != 0 {
                let instruction_len = usize::from(read_u16(data, offset)?);
                offset =
                    offset
                        .checked_add(2 + instruction_len)
                        .ok_or(FontError::MalformedTrueType(
                            "composite instruction overflow",
                        ))?;
                if offset > data.len() {
                    return Err(FontError::MalformedTrueType(
                        "truncated composite instructions",
                    ));
                }
            }
            break;
        }
    }

    Ok(GlyphStats {
        points,
        contours,
        depth,
    })
}

impl ParsedFont {
    fn parse(font: &[u8]) -> Result<Self, FontError> {
        if font.len() < 12 {
            return Err(FontError::MalformedTrueType("sfnt header is too short"));
        }
        if font.get(..4) == Some(b"ttcf") {
            return Err(FontError::UnsupportedFontCollection);
        }
        let scaler_type = read_u32(font, 0)?;
        let num_tables = usize::from(read_u16(font, 4)?);
        let directory_end = 12_usize
            .checked_add(num_tables.checked_mul(16).ok_or(FontError::FontTooLarge)?)
            .ok_or(FontError::FontTooLarge)?;
        if directory_end > font.len() {
            return Err(FontError::MalformedTrueType("table directory is truncated"));
        }

        let mut tables = BTreeMap::new();
        for index in 0..num_tables {
            let base = 12 + index * 16;
            let tag = font
                .get(base..base + 4)
                .ok_or(FontError::MalformedTrueType("table tag is truncated"))?
                .try_into()
                .map_err(|_| FontError::MalformedTrueType("invalid table tag"))?;
            let offset =
                usize::try_from(read_u32(font, base + 8)?).map_err(|_| FontError::FontTooLarge)?;
            let length =
                usize::try_from(read_u32(font, base + 12)?).map_err(|_| FontError::FontTooLarge)?;
            let end = offset.checked_add(length).ok_or(FontError::FontTooLarge)?;
            let data = font
                .get(offset..end)
                .ok_or(FontError::MalformedTrueType("table extends beyond font"))?;
            tables.insert(tag, data.to_vec());
        }

        for required in [TAG_HEAD, TAG_HHEA, TAG_HMTX, TAG_LOCA, TAG_GLYF, TAG_MAXP] {
            if !tables.contains_key(&required) {
                return Err(FontError::MissingTrueTypeTable(required));
            }
        }

        Ok(Self {
            scaler_type,
            tables,
        })
    }

    fn build(mut self) -> Result<Vec<u8>, FontError> {
        let mut head = required_table(&self.tables, TAG_HEAD)?.clone();
        write_u32(&mut head, 8, 0)?;
        self.tables.insert(TAG_HEAD, head);

        let tables: Vec<Table> = self
            .tables
            .into_iter()
            .map(|(tag, data)| Table { tag, data })
            .collect();
        let num_tables = u16::try_from(tables.len()).map_err(|_| FontError::TooManyTables)?;
        let directory_len = 12_usize
            .checked_add(
                tables
                    .len()
                    .checked_mul(16)
                    .ok_or(FontError::FontTooLarge)?,
            )
            .ok_or(FontError::FontTooLarge)?;

        let mut offsets = Vec::with_capacity(tables.len());
        let mut cursor = align4(directory_len);
        for table in &tables {
            offsets.push(cursor);
            cursor = align4(
                cursor
                    .checked_add(table.data.len())
                    .ok_or(FontError::FontTooLarge)?,
            );
        }
        if cursor > usize::try_from(u32::MAX).unwrap_or(usize::MAX) {
            return Err(FontError::FontTooLarge);
        }

        let mut output = vec![0_u8; cursor];
        write_u32(&mut output, 0, self.scaler_type)?;
        write_u16(&mut output, 4, num_tables)?;
        let (search_range, entry_selector, range_shift) = sfnt_search_fields(num_tables);
        write_u16(&mut output, 6, search_range)?;
        write_u16(&mut output, 8, entry_selector)?;
        write_u16(&mut output, 10, range_shift)?;

        let mut head_offset = None;
        for (index, (table, offset)) in tables.iter().zip(&offsets).enumerate() {
            let directory = 12 + index * 16;
            output[directory..directory + 4].copy_from_slice(&table.tag);
            write_u32(&mut output, directory + 4, table_checksum(&table.data))?;
            write_u32(
                &mut output,
                directory + 8,
                u32::try_from(*offset).map_err(|_| FontError::FontTooLarge)?,
            )?;
            write_u32(
                &mut output,
                directory + 12,
                u32::try_from(table.data.len()).map_err(|_| FontError::FontTooLarge)?,
            )?;
            let end = offset + table.data.len();
            output[*offset..end].copy_from_slice(&table.data);
            if table.tag == TAG_HEAD {
                head_offset = Some(*offset);
            }
        }

        let head_offset = head_offset.ok_or(FontError::MissingTrueTypeTable(TAG_HEAD))?;
        let sum = table_checksum(&output);
        let adjustment = CHECKSUM_MAGIC.wrapping_sub(sum);
        write_u32(&mut output, head_offset + 8, adjustment)?;

        Ok(output)
    }
}

fn required_table(
    tables: &BTreeMap<[u8; 4], Vec<u8>>,
    tag: [u8; 4],
) -> Result<&Vec<u8>, FontError> {
    tables.get(&tag).ok_or(FontError::MissingTrueTypeTable(tag))
}

fn parse_loca(
    data: &[u8],
    glyph_count: u16,
    format: i16,
    glyf_len: usize,
) -> Result<Vec<u32>, FontError> {
    let count = usize::from(glyph_count) + 1;
    let mut offsets = Vec::with_capacity(count);
    for index in 0..count {
        let offset = if format == 0 {
            u32::from(read_u16(data, index * 2)?) * 2
        } else {
            read_u32(data, index * 4)?
        };
        if let Some(previous) = offsets.last().copied() {
            if offset < previous {
                return Err(FontError::MalformedTrueType(
                    "loca offsets are not monotonic",
                ));
            }
        }
        if usize::try_from(offset).map_err(|_| FontError::FontTooLarge)? > glyf_len {
            return Err(FontError::MalformedTrueType(
                "loca offset exceeds glyf table",
            ));
        }
        offsets.push(offset);
    }
    Ok(offsets)
}

fn parse_hmetrics(
    tables: &BTreeMap<[u8; 4], Vec<u8>>,
    glyph_count: u16,
) -> Result<Vec<(u16, i16)>, FontError> {
    let hhea = required_table(tables, TAG_HHEA)?;
    if hhea.len() < 36 {
        return Err(FontError::MalformedTrueType("hhea table is too short"));
    }
    let metric_count = read_u16(hhea, 34)?;
    if metric_count == 0 || metric_count > glyph_count {
        return Err(FontError::MalformedTrueType("invalid numberOfHMetrics"));
    }
    let hmtx = required_table(tables, TAG_HMTX)?;
    let mut metrics = Vec::with_capacity(usize::from(glyph_count));
    let mut last_advance = 0_u16;
    for gid in 0..glyph_count {
        if gid < metric_count {
            let offset = usize::from(gid) * 4;
            let advance = read_u16(hmtx, offset)?;
            let lsb = read_i16(hmtx, offset + 2)?;
            last_advance = advance;
            metrics.push((advance, lsb));
        } else {
            let lsb_index = usize::from(gid - metric_count);
            let offset = usize::from(metric_count) * 4 + lsb_index * 2;
            metrics.push((last_advance, read_i16(hmtx, offset)?));
        }
    }
    Ok(metrics)
}

fn serialize_hmetrics(metrics: &[(u16, i16)]) -> Vec<u8> {
    let mut output = Vec::with_capacity(metrics.len() * 4);
    for &(advance, lsb) in metrics {
        push_u16(&mut output, advance);
        push_i16(&mut output, lsb);
    }
    output
}

fn serialize_long_loca(offsets: &[u32]) -> Vec<u8> {
    let mut output = Vec::with_capacity(offsets.len() * 4);
    for &offset in offsets {
        push_u32(&mut output, offset);
    }
    output
}

fn sfnt_search_fields(num_tables: u16) -> (u16, u16, u16) {
    if num_tables == 0 {
        return (0, 0, 0);
    }
    let mut power = 1_u16;
    let mut selector = 0_u16;
    while power <= num_tables / 2 {
        power *= 2;
        selector += 1;
    }
    let search_range = power * 16;
    let range_shift = num_tables * 16 - search_range;
    (search_range, selector, range_shift)
}

fn table_checksum(data: &[u8]) -> u32 {
    let mut sum = 0_u32;
    for chunk in data.chunks(4) {
        let mut word = [0_u8; 4];
        word[..chunk.len()].copy_from_slice(chunk);
        sum = sum.wrapping_add(u32::from_be_bytes(word));
    }
    sum
}

fn align4(value: usize) -> usize {
    (value + 3) & !3
}

fn saturating_u16(value: u32) -> u16 {
    u16::try_from(value).unwrap_or(u16::MAX)
}

fn checked_i16(value: i32) -> Result<i16, FontError> {
    i16::try_from(value).map_err(|_| FontError::CoordinateOutOfRange(value))
}

fn read_u16(data: &[u8], offset: usize) -> Result<u16, FontError> {
    let bytes = data
        .get(offset..offset + 2)
        .ok_or(FontError::MalformedTrueType("unexpected end of table"))?;
    Ok(u16::from_be_bytes([bytes[0], bytes[1]]))
}

fn read_i16(data: &[u8], offset: usize) -> Result<i16, FontError> {
    Ok(i16::from_be_bytes(read_u16(data, offset)?.to_be_bytes()))
}

fn read_u32(data: &[u8], offset: usize) -> Result<u32, FontError> {
    let bytes = data
        .get(offset..offset + 4)
        .ok_or(FontError::MalformedTrueType("unexpected end of table"))?;
    Ok(u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

fn write_u16(data: &mut [u8], offset: usize, value: u16) -> Result<(), FontError> {
    let target = data
        .get_mut(offset..offset + 2)
        .ok_or(FontError::MalformedTrueType("write exceeds table"))?;
    target.copy_from_slice(&value.to_be_bytes());
    Ok(())
}

fn write_i16(data: &mut [u8], offset: usize, value: i16) -> Result<(), FontError> {
    write_u16(data, offset, u16::from_be_bytes(value.to_be_bytes()))
}

fn write_u32(data: &mut [u8], offset: usize, value: u32) -> Result<(), FontError> {
    let target = data
        .get_mut(offset..offset + 4)
        .ok_or(FontError::MalformedTrueType("write exceeds table"))?;
    target.copy_from_slice(&value.to_be_bytes());
    Ok(())
}

fn push_u16(data: &mut Vec<u8>, value: u16) {
    data.extend_from_slice(&value.to_be_bytes());
}

fn push_i16(data: &mut Vec<u8>, value: i16) {
    data.extend_from_slice(&value.to_be_bytes());
}

fn push_u32(data: &mut Vec<u8>, value: u32) {
    data.extend_from_slice(&value.to_be_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checksum_is_word_padded() {
        assert_eq!(table_checksum(&[1, 2, 3, 4]), 0x0102_0304);
        assert_eq!(table_checksum(&[1, 2, 3, 4, 5]), 0x0602_0304);
    }

    #[test]
    fn search_fields_follow_sfnt_rule() {
        assert_eq!(sfnt_search_fields(1), (16, 0, 0));
        assert_eq!(sfnt_search_fields(5), (64, 2, 16));
    }
}
