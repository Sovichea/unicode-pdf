#!/usr/bin/env python3
"""Create a deliberate line-local Khmer raster distortion for metric calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def distort_line(
    source: Path,
    output: Path,
    *,
    width_fraction: float,
    shift_x: int,
    ink_threshold: int,
    max_blank_row_gap: int,
) -> dict[str, int | float]:
    width, height, source_pixels = read_ppm(source)
    bands = detect_ink_bands(
        width,
        height,
        source_pixels,
        source_pixels,
        ink_threshold=ink_threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    if not bands:
        raise RuntimeError(f"no rendered-ink band found in {source}")

    target_band_index = len(bands) // 2
    top, bottom = bands[target_band_index]
    xs: list[int] = []
    for y in range(top, bottom + 1):
        for x in range(width):
            offset = (y * width + x) * 3
            if pixel_is_ink(source_pixels, offset, ink_threshold):
                xs.append(x)
    if not xs:
        raise RuntimeError(f"selected ink band is empty in {source}")

    left, right = min(xs), max(xs)
    ink_width = right - left + 1
    distorted_width = max(1, round(ink_width * width_fraction))
    region_left = left + max(0, (ink_width - distorted_width) // 2)
    region_right = min(right, region_left + distorted_width - 1)

    output_pixels = bytearray(source_pixels)
    white = b"\xff\xff\xff"
    source_copy = bytes(source_pixels)
    for y in range(top, bottom + 1):
        for x in range(region_left, region_right + 1):
            offset = (y * width + x) * 3
            if pixel_is_ink(source_copy, offset, ink_threshold):
                output_pixels[offset : offset + 3] = white

    moved_pixels = 0
    for y in range(top, bottom + 1):
        for x in range(region_left, region_right + 1):
            source_offset = (y * width + x) * 3
            if not pixel_is_ink(source_copy, source_offset, ink_threshold):
                continue
            destination_x = x + shift_x
            if 0 <= destination_x < width:
                destination_offset = (y * width + destination_x) * 3
                output_pixels[destination_offset : destination_offset + 3] = source_copy[
                    source_offset : source_offset + 3
                ]
                moved_pixels += 1

    write_ppm(output, width, height, bytes(output_pixels))
    return {
        "line_count": len(bands),
        "target_line": target_band_index + 1,
        "top": top,
        "bottom": bottom,
        "region_left": region_left,
        "region_right": region_right,
        "width_fraction": width_fraction,
        "shift_x": shift_x,
        "moved_ink_pixels": moved_pixels,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--width-fraction", type=float, default=0.10)
    parser.add_argument("--shift-x", type=int, default=2)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    return parser.parse_args()


def fraction_label(value: float) -> str:
    return f"{value:.3f}".replace(".", "p")


def signed_label(value: int) -> str:
    return f"{value:+d}"


def main() -> int:
    args = parse_args()
    if not 0.0 < args.width_fraction <= 1.0:
        raise RuntimeError("--width-fraction must be in (0, 1]")
    if args.shift_x == 0:
        raise RuntimeError("--shift-x must be non-zero")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, args.reference_prefix)
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    variant = f"sensitivity-line-w{fraction_label(args.width_fraction)}-x{signed_label(args.shift_x)}"
    output_dir = directory / variant
    candidate_prefix = f"system-harfbuzz-line-w{fraction_label(args.width_fraction)}-x{signed_label(args.shift_x)}"
    pages: list[dict[str, int | float]] = []
    for page_number, source in enumerate(reference_pages, start=1):
        output = output_dir / f"{candidate_prefix}-{page_number}.ppm"
        pages.append(
            {
                "page": page_number,
                **distort_line(
                    source,
                    output,
                    width_fraction=args.width_fraction,
                    shift_x=args.shift_x,
                    ink_threshold=args.ink_threshold,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": f"{variant}/{candidate_prefix}",
        "width_fraction": args.width_fraction,
        "shift_x": args.shift_x,
        "ink_threshold": args.ink_threshold,
        "max_blank_row_gap": args.max_blank_row_gap,
        "pages": pages,
    }
    output_path = directory / f"sensitivity-results-line-w{fraction_label(args.width_fraction)}-x{signed_label(args.shift_x)}.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
