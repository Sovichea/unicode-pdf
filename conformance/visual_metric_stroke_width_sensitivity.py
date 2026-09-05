#!/usr/bin/env python3
"""Create a localized Khmer stroke-thickening defect for visual calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def thicken_line_region(
    source: Path,
    output: Path,
    *,
    width_fraction: float,
    x_position: float,
    passes: int,
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
    xs = [
        x
        for y in range(top, bottom + 1)
        for x in range(width)
        if pixel_is_ink(source_pixels, (y * width + x) * 3, ink_threshold)
    ]
    if not xs:
        raise RuntimeError(f"selected ink band is empty in {source}")

    left, right = min(xs), max(xs)
    ink_width = right - left + 1
    region_width = max(3, round(ink_width * width_fraction))
    region_left = left + round((ink_width - region_width) * x_position)
    region_right = min(right, region_left + region_width - 1)

    original_ink = [
        pixel_is_ink(source_pixels, offset, ink_threshold)
        for offset in range(0, len(source_pixels), 3)
    ]
    current_ink = original_ink[:]
    for _ in range(passes):
        previous = current_ink[:]
        for y in range(top, bottom + 1):
            for x in range(region_left, region_right + 1):
                index = y * width + x
                if previous[index]:
                    continue
                neighbors = []
                if x > 0:
                    neighbors.append(previous[index - 1])
                if x + 1 < width:
                    neighbors.append(previous[index + 1])
                if y > 0:
                    neighbors.append(previous[index - width])
                if y + 1 < height:
                    neighbors.append(previous[index + width])
                if any(neighbors):
                    current_ink[index] = True

    current = bytearray(source_pixels)
    changed_pixels = 0
    for y in range(top, bottom + 1):
        for x in range(region_left, region_right + 1):
            index = y * width + x
            if current_ink[index] and not original_ink[index]:
                offset = index * 3
                current[offset : offset + 3] = b"\x00\x00\x00"
                changed_pixels += 1

    if changed_pixels == 0:
        raise RuntimeError("stroke-thickening mutation changed no pixels")

    write_ppm(output, width, height, bytes(current))
    return {
        "line_count": len(bands),
        "target_line": target_band_index + 1,
        "top": top,
        "bottom": bottom,
        "region_left": region_left,
        "region_right": region_right,
        "width_fraction": width_fraction,
        "x_position": x_position,
        "passes": passes,
        "changed_pixels": changed_pixels,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--width-fraction", type=float, default=0.10)
    parser.add_argument("--x-position", type=float, default=0.40)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    return parser.parse_args()


def label(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", "p")


def main() -> int:
    args = parse_args()
    if not 0.0 < args.width_fraction <= 1.0:
        raise RuntimeError("--width-fraction must be in (0, 1]")
    if not 0.0 <= args.x_position <= 1.0:
        raise RuntimeError("--x-position must be in [0, 1]")
    if args.passes < 1:
        raise RuntimeError("--passes must be >= 1")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, args.reference_prefix)
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    width_label = label(args.width_fraction, 3)
    x_label = label(args.x_position, 2)
    variant = f"sensitivity-stroke-width-w{width_label}-xp{x_label}-p{args.passes}"
    candidate_prefix = f"system-harfbuzz-stroke-width-w{width_label}-xp{x_label}-p{args.passes}"
    output_dir = directory / variant

    pages: list[dict[str, int | float]] = []
    for page_number, source in enumerate(reference_pages, start=1):
        output = output_dir / f"{candidate_prefix}-{page_number}.ppm"
        pages.append(
            {
                "page": page_number,
                **thicken_line_region(
                    source,
                    output,
                    width_fraction=args.width_fraction,
                    x_position=args.x_position,
                    passes=args.passes,
                    ink_threshold=args.ink_threshold,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": f"{variant}/{candidate_prefix}",
        "width_fraction": args.width_fraction,
        "x_position": args.x_position,
        "passes": args.passes,
        "pages": pages,
    }
    result_path = directory / (
        f"sensitivity-results-stroke-width-w{width_label}-xp{x_label}-p{args.passes}.json"
    )
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
