#!/usr/bin/env python3
"""Create a localized one-sided Khmer antialias halo defect for visual calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def add_right_halo(
    source: Path,
    output: Path,
    *,
    width_fraction: float,
    x_position: float,
    core_threshold: int,
    halo_value: int,
    max_blank_row_gap: int,
) -> dict[str, int | float]:
    width, height, source_pixels = read_ppm(source)
    bands = detect_ink_bands(
        width,
        height,
        source_pixels,
        source_pixels,
        ink_threshold=250,
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
        if pixel_is_ink(source_pixels, (y * width + x) * 3, core_threshold)
    ]
    if not xs:
        raise RuntimeError(f"selected ink band has no core pixels in {source}")

    left, right = min(xs), max(xs)
    ink_width = right - left + 1
    region_width = max(3, round(ink_width * width_fraction))
    region_left = left + round((ink_width - region_width) * x_position)
    region_right = min(right, region_left + region_width - 1)

    current = bytearray(source_pixels)
    changed: set[tuple[int, int]] = set()
    for y in range(top, bottom + 1):
        for x in range(region_left, min(region_right, width - 2) + 1):
            offset = (y * width + x) * 3
            if not pixel_is_ink(source_pixels, offset, core_threshold):
                continue
            nx = x + 1
            neighbor = (y * width + nx) * 3
            if pixel_is_ink(source_pixels, neighbor, core_threshold):
                continue
            original = max(source_pixels[neighbor : neighbor + 3])
            if original <= halo_value:
                continue
            for channel in range(3):
                current[neighbor + channel] = min(current[neighbor + channel], halo_value)
            changed.add((nx, y))

    if not changed:
        raise RuntimeError("one-sided halo mutation changed no pixels")

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
        "core_threshold": core_threshold,
        "halo_value": halo_value,
        "changed_pixels": len(changed),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--width-fraction", type=float, default=0.12)
    parser.add_argument("--x-position", type=float, default=0.40)
    parser.add_argument("--core-threshold", type=int, default=200)
    parser.add_argument("--halo-value", type=int, default=210)
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
    if not 0 <= args.core_threshold <= 255:
        raise RuntimeError("--core-threshold must be in [0, 255]")
    if not args.core_threshold < args.halo_value <= 255:
        raise RuntimeError("--halo-value must be above core-threshold and <= 255")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, args.reference_prefix)
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    width_label = label(args.width_fraction, 3)
    x_label = label(args.x_position, 2)
    variant = f"sensitivity-halo-w{width_label}-xp{x_label}-v{args.halo_value}"
    candidate_prefix = f"system-harfbuzz-halo-w{width_label}-xp{x_label}-v{args.halo_value}"
    output_dir = directory / variant

    pages: list[dict[str, int | float]] = []
    for page_number, source in enumerate(reference_pages, start=1):
        output = output_dir / f"{candidate_prefix}-{page_number}.ppm"
        pages.append(
            {
                "page": page_number,
                **add_right_halo(
                    source,
                    output,
                    width_fraction=args.width_fraction,
                    x_position=args.x_position,
                    core_threshold=args.core_threshold,
                    halo_value=args.halo_value,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": f"{variant}/{candidate_prefix}",
        "width_fraction": args.width_fraction,
        "x_position": args.x_position,
        "core_threshold": args.core_threshold,
        "halo_value": args.halo_value,
        "pages": pages,
    }
    result_path = directory / f"sensitivity-results-halo-w{width_label}-xp{x_label}-v{args.halo_value}.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
