#!/usr/bin/env python3
"""Create a deliberate Khmer line-local vertical mark displacement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def zone_bounds(top: int, bottom: int, zone: str, zone_fraction: float) -> tuple[int, int]:
    line_height = bottom - top + 1
    zone_height = max(1, round(line_height * zone_fraction))
    if zone == "upper":
        return top, min(bottom, top + zone_height - 1)
    return max(top, bottom - zone_height + 1), bottom


def distort(
    source: Path,
    output: Path,
    *,
    zone: str,
    zone_fraction: float,
    shift_y: int,
    ink_threshold: int,
    max_blank_row_gap: int,
) -> dict[str, int | float | str]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(
        width,
        height,
        pixels,
        pixels,
        ink_threshold=ink_threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    if not bands:
        raise RuntimeError(f"no rendered-ink band found in {source}")

    target_index = len(bands) // 2
    top, bottom = bands[target_index]
    zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)

    source_copy = bytes(pixels)
    output_pixels = bytearray(pixels)
    white = b"\xff\xff\xff"
    moved = 0

    for y in range(zone_top, zone_bottom + 1):
        for x in range(width):
            offset = (y * width + x) * 3
            if pixel_is_ink(source_copy, offset, ink_threshold):
                output_pixels[offset : offset + 3] = white

    for y in range(zone_top, zone_bottom + 1):
        for x in range(width):
            source_offset = (y * width + x) * 3
            if not pixel_is_ink(source_copy, source_offset, ink_threshold):
                continue
            destination_y = y + shift_y
            if 0 <= destination_y < height:
                destination_offset = (destination_y * width + x) * 3
                output_pixels[destination_offset : destination_offset + 3] = source_copy[
                    source_offset : source_offset + 3
                ]
                moved += 1

    if moved == 0:
        raise RuntimeError(f"selected vertical zone contained no ink in {source}")

    write_ppm(output, width, height, bytes(output_pixels))
    return {
        "line_count": len(bands),
        "target_line": target_index + 1,
        "top": top,
        "bottom": bottom,
        "zone": zone,
        "zone_top": zone_top,
        "zone_bottom": zone_bottom,
        "zone_fraction": zone_fraction,
        "shift_y": shift_y,
        "moved_ink_pixels": moved,
    }


def label_fraction(value: float) -> str:
    return f"{value:.3f}".replace(".", "p")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--zone", choices=("upper", "lower"), default="upper")
    parser.add_argument("--zone-fraction", type=float, default=0.25)
    parser.add_argument("--shift-y", type=int, default=2)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    args = parser.parse_args()

    if not 0.0 < args.zone_fraction <= 1.0:
        raise RuntimeError("--zone-fraction must be in (0, 1]")
    if args.shift_y == 0:
        raise RuntimeError("--shift-y must be non-zero")

    directory = Path(args.dir)
    sources = numbered_pages(directory, args.reference_prefix)
    if not sources:
        raise RuntimeError(f"no reference rasters found in {directory}")

    fraction = label_fraction(args.zone_fraction)
    zone_label = "" if args.zone == "upper" else f"-{args.zone}"
    variant = f"sensitivity-vertical{zone_label}-z{fraction}-y{args.shift_y:+d}"
    candidate_prefix = f"system-harfbuzz-vertical{zone_label}-z{fraction}-y{args.shift_y:+d}"
    output_dir = directory / variant
    pages: list[dict[str, int | float | str]] = []
    for page_number, source in enumerate(sources, start=1):
        destination = output_dir / f"{candidate_prefix}-{page_number}.ppm"
        pages.append(
            {
                "page": page_number,
                **distort(
                    source,
                    destination,
                    zone=args.zone,
                    zone_fraction=args.zone_fraction,
                    shift_y=args.shift_y,
                    ink_threshold=args.ink_threshold,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": f"{variant}/{candidate_prefix}",
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "shift_y": args.shift_y,
        "pages": pages,
    }
    output = directory / f"sensitivity-results-vertical{zone_label}-z{fraction}-y{args.shift_y:+d}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
