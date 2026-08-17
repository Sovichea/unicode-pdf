#!/usr/bin/env python3
"""Generate a proof-of-concept logical-cluster CID PDF.

This is experiment code, not the production PDF writer. It intentionally uses
FontTools plus the system HarfBuzz shared library so the PDF model can be tested
without requiring a Rust shaping/font-subsetting stack first.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import io
from pathlib import Path
import zlib

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent


class HbGlyphInfo(ctypes.Structure):
    _fields_ = [
        ("codepoint", ctypes.c_uint32),
        ("mask", ctypes.c_uint32),
        ("cluster", ctypes.c_uint32),
        ("var1", ctypes.c_uint32),
        ("var2", ctypes.c_uint32),
    ]


class HbGlyphPosition(ctypes.Structure):
    _fields_ = [
        ("x_advance", ctypes.c_int32),
        ("y_advance", ctypes.c_int32),
        ("x_offset", ctypes.c_int32),
        ("y_offset", ctypes.c_int32),
        ("var", ctypes.c_uint32),
    ]


def load_harfbuzz():
    library_name = ctypes.util.find_library("harfbuzz")
    if not library_name:
        raise RuntimeError("system HarfBuzz shared library was not found")
    lib = ctypes.CDLL(library_name)
    signatures = [
        ("hb_blob_create_from_file_or_fail", ctypes.c_void_p, [ctypes.c_char_p]),
        ("hb_face_create", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_uint]),
        ("hb_font_create", ctypes.c_void_p, [ctypes.c_void_p]),
        ("hb_ot_font_set_funcs", None, [ctypes.c_void_p]),
        ("hb_face_get_upem", ctypes.c_uint, [ctypes.c_void_p]),
        ("hb_font_set_scale", None, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
        ("hb_buffer_create", ctypes.c_void_p, []),
        (
            "hb_buffer_add_utf8",
            None,
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_int],
        ),
        ("hb_buffer_guess_segment_properties", None, [ctypes.c_void_p]),
        ("hb_shape", None, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]),
        (
            "hb_buffer_get_glyph_infos",
            ctypes.POINTER(HbGlyphInfo),
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        ),
        (
            "hb_buffer_get_glyph_positions",
            ctypes.POINTER(HbGlyphPosition),
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        ),
        ("hb_buffer_destroy", None, [ctypes.c_void_p]),
        ("hb_font_destroy", None, [ctypes.c_void_p]),
        ("hb_face_destroy", None, [ctypes.c_void_p]),
        ("hb_blob_destroy", None, [ctypes.c_void_p]),
    ]
    for name, result, args in signatures:
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = args
    return lib


HB = load_harfbuzz()


def shape_run(text: str, font_path: Path):
    encoded = text.encode("utf-8")
    blob = HB.hb_blob_create_from_file_or_fail(str(font_path).encode())
    if not blob:
        raise RuntimeError(f"HarfBuzz could not open font: {font_path}")
    face = HB.hb_face_create(blob, 0)
    font = HB.hb_font_create(face)
    HB.hb_ot_font_set_funcs(font)
    upem = HB.hb_face_get_upem(face)
    HB.hb_font_set_scale(font, upem, upem)
    buffer = HB.hb_buffer_create()
    HB.hb_buffer_add_utf8(buffer, encoded, len(encoded), 0, len(encoded))
    HB.hb_buffer_guess_segment_properties(buffer)
    HB.hb_shape(font, buffer, None, 0)

    count = ctypes.c_uint()
    infos = HB.hb_buffer_get_glyph_infos(buffer, ctypes.byref(count))
    positions = HB.hb_buffer_get_glyph_positions(buffer, ctypes.byref(count))

    glyphs = []
    pen_x = 0
    pen_y = 0
    for index in range(count.value):
        glyphs.append(
            {
                "gid": infos[index].codepoint,
                "cluster": infos[index].cluster,
                "x_advance": positions[index].x_advance,
                "y_advance": positions[index].y_advance,
                "x_offset": positions[index].x_offset,
                "y_offset": positions[index].y_offset,
                "pen_x": pen_x,
                "pen_y": pen_y,
            }
        )
        pen_x += positions[index].x_advance
        pen_y += positions[index].y_advance

    starts = sorted({glyph["cluster"] for glyph in glyphs} | {len(encoded)})
    spans = []
    for index, start in enumerate(starts[:-1]):
        end = starts[index + 1]
        spans.append(
            {
                "start": start,
                "end": end,
                "text": encoded[start:end].decode("utf-8"),
            }
        )

    groups = {start: [] for start in starts[:-1]}
    for glyph in glyphs:
        groups[glyph["cluster"]].append(glyph)

    HB.hb_buffer_destroy(buffer)
    HB.hb_font_destroy(font)
    HB.hb_face_destroy(face)
    HB.hb_blob_destroy(blob)

    return upem, spans, groups, pen_x


def make_synthetic_font(text: str, source_font: Path, output_font: Path):
    upem, spans, groups, total_advance = shape_run(text, source_font)
    font = TTFont(str(source_font))
    base_order = list(font.getGlyphOrder())
    glyf = font["glyf"]
    hmtx = font["hmtx"]
    added = []
    records = []

    for index, span in enumerate(spans):
        source_glyphs = groups[span["start"]]
        name = f"logical{index:04d}"
        composite = Glyph()
        composite.numberOfContours = -1
        composite.components = []

        if source_glyphs:
            visual_origin = min(glyph["pen_x"] for glyph in source_glyphs)
            visual_end = max(
                glyph["pen_x"] + glyph["x_advance"] for glyph in source_glyphs
            )
            for shaped in source_glyphs:
                component = GlyphComponent()
                component.glyphName = base_order[shaped["gid"]]
                component.x = round(
                    shaped["pen_x"] - visual_origin + shaped["x_offset"]
                )
                component.y = round(shaped["pen_y"] + shaped["y_offset"])
                component.flags = 4
                composite.components.append(component)
            advance = max(0, visual_end - visual_origin)
        else:
            visual_origin = 0
            advance = 0

        glyf.glyphs[name] = composite
        hmtx.metrics[name] = (round(advance), 0)
        added.append(name)
        records.append(
            {
                "unicode": span["text"],
                "visual_x": visual_origin,
                "advance": advance,
                "components": len(source_glyphs),
            }
        )

    font.setGlyphOrder(base_order + added)
    font["maxp"].numGlyphs = len(base_order) + len(added)
    font["post"].formatType = 3.0
    for tag in ("GSUB", "GPOS", "GDEF", "DSIG"):
        if tag in font:
            del font[tag]

    for index, record in enumerate(records):
        record["gid"] = len(base_order) + index

    output_font.parent.mkdir(parents=True, exist_ok=True)
    font.save(output_font)
    return upem, records, total_advance


class PdfBuilder:
    def __init__(self):
        self.objects: list[bytes] = []

    def add(self, data=b"") -> int:
        if isinstance(data, str):
            data = data.encode("latin1")
        self.objects.append(data)
        return len(self.objects)

    def set(self, number: int, data) -> None:
        if isinstance(data, str):
            data = data.encode("latin1")
        self.objects[number - 1] = data

    def stream(self, dictionary: str, data: bytes, compress: bool = False) -> int:
        if compress:
            data = zlib.compress(data, 9)
            dictionary += " /Filter /FlateDecode"
        return self.add(
            f"<< {dictionary} /Length {len(data)} >>\nstream\n".encode()
            + data
            + b"\nendstream"
        )

    def save(self, path: Path, root: int) -> None:
        output = io.BytesIO()
        output.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for number, data in enumerate(self.objects, 1):
            offsets.append(output.tell())
            output.write(f"{number} 0 obj\n".encode())
            output.write(data)
            output.write(b"\nendobj\n")
        xref = output.tell()
        output.write(f"xref\n0 {len(self.objects) + 1}\n".encode())
        output.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.write(f"{offset:010d} 00000 n \n".encode())
        output.write(
            (
                f"trailer\n<< /Size {len(self.objects) + 1} /Root {root} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n"
            ).encode()
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(output.getvalue())


def unicode_hex(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def build_cmap(records) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /UnicodePdfLogicalText def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    for offset in range(0, len(records), 100):
        chunk = records[offset : offset + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for cid_offset, record in enumerate(chunk, offset + 1):
            lines.append(f"<{cid_offset:04X}> <{unicode_hex(record['unicode'])}>")
        lines.append("endbfchar")
    lines += [
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def build_pdf(text: str, source_font: Path, output_pdf: Path, font_size: float) -> None:
    synthetic_font = output_pdf.with_suffix(".synthetic.ttf")
    upem, records, total_advance = make_synthetic_font(text, source_font, synthetic_font)

    subset = TTFont(synthetic_font)
    head = subset["head"]
    hhea = subset["hhea"]
    os2 = subset["OS/2"]
    scale = 1000 / upem
    bbox = [
        round(head.xMin * scale),
        round(head.yMin * scale),
        round(head.xMax * scale),
        round(head.yMax * scale),
    ]
    ascent = round(hhea.ascent * scale)
    descent = round(hhea.descent * scale)
    cap_height = round(getattr(os2, "sCapHeight", hhea.ascent) * scale)

    pdf = PdfBuilder()
    catalog = pdf.add()
    pages = pdf.add()
    page = pdf.add()

    raw_font = synthetic_font.read_bytes()
    font_file = pdf.stream(f"/Length1 {len(raw_font)}", raw_font, compress=True)
    descriptor = pdf.add(
        "<< /Type /FontDescriptor /FontName /UnicodePdfSynthetic "
        f"/Flags 4 /FontBBox [{' '.join(map(str, bbox))}] /ItalicAngle 0 "
        f"/Ascent {ascent} /Descent {descent} /CapHeight {cap_height} "
        f"/StemV 80 /FontFile2 {font_file} 0 R >>"
    )

    gid_map = bytearray((len(records) + 1) * 2)
    for cid, record in enumerate(records, 1):
        gid_map[cid * 2 : cid * 2 + 2] = int(record["gid"]).to_bytes(2, "big")
    gid_map_object = pdf.stream("", bytes(gid_map), compress=True)
    widths = " ".join(str(round(record["advance"] * 1000 / upem)) for record in records)
    cid_font = pdf.add(
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /UnicodePdfSynthetic "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor {descriptor} 0 R /DW 0 /W [1 [{widths}]] "
        f"/CIDToGIDMap {gid_map_object} 0 R >>"
    )
    to_unicode = pdf.stream("", build_cmap(records))
    type0 = pdf.add(
        "<< /Type /Font /Subtype /Type0 /BaseFont /UnicodePdfSynthetic "
        "/Encoding /Identity-H "
        f"/DescendantFonts [{cid_font} 0 R] /ToUnicode {to_unicode} 0 R >>"
    )

    margin = 36.0
    page_width = max(500.0, margin * 2 + abs(total_advance) / upem * font_size)
    page_height = 120.0
    baseline = 65.0
    operators = ["BT", f"/F1 {font_size:.2f} Tf", "0 Tr"]

    # Operators remain in logical source order. Each logical CID is placed at
    # the visual X coordinate derived from the complete HarfBuzz-shaped run.
    min_visual_x = min((record["visual_x"] for record in records), default=0)
    for cid, record in enumerate(records, 1):
        x = margin + (record["visual_x"] - min_visual_x) / upem * font_size
        operators.append(f"1 0 0 1 {x:.4f} {baseline:.4f} Tm <{cid:04X}> Tj")
    operators.append("ET")
    content = pdf.stream("", ("\n".join(operators) + "\n").encode("ascii"))

    pdf.set(
        page,
        "<< /Type /Page "
        f"/Parent {pages} 0 R /MediaBox [0 0 {page_width:.4f} {page_height:.4f}] "
        f"/Resources << /Font << /F1 {type0} 0 R >> >> /Contents {content} 0 R >>",
    )
    pdf.set(pages, f"<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>")
    pdf.set(catalog, f"<< /Type /Catalog /Pages {pages} 0 R >>")
    pdf.save(output_pdf, catalog)

    manifest = output_pdf.with_suffix(".units.txt")
    manifest.write_text(
        "\n".join(
            f"CID {index:04X} | {record['unicode']!r} | "
            f"gid={record['gid']} components={record['components']} "
            f"visual_x={record['visual_x']} advance={record['advance']}"
            for index, record in enumerate(records, 1)
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"wrote {output_pdf}")
    print(f"wrote {synthetic_font}")
    print(f"wrote {manifest}")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", type=Path, required=True, help="source TrueType font")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="logical UTF-8 text")
    source.add_argument("--text-file", type=Path, help="UTF-8 text file")
    parser.add_argument("--out", type=Path, required=True, help="output PDF")
    parser.add_argument("--font-size", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8").rstrip("\n")
    else:
        text = args.text
    if not text:
        raise SystemExit("input text is empty")
    build_pdf(text, args.font, args.out, args.font_size)


if __name__ == "__main__":
    main()
