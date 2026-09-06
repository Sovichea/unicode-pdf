#!/usr/bin/env python3
"""Create a line-local Khmer tone distortion without changing the ink mask."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def distort_line_tone(
    source: Path,
    output: Path,
    *,
    width_fraction: float,
    x_position: float,
    tone: int,
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
    region_left = left + round((ink_width - distorted_width) * x_position)
    region_right = min(right, region_left + distorted_width - 1)

    output_pixels = bytearray(source_pixels)
    toned_pixels = 0
    replacement = bytes((tone, tone, tone))
    for y in range(top, bottom + 1):
        for x in range(region_left, region_right + 1):
            offset = (y * width + x) * 3
            if not pixel_is_ink(source_pixels, offset, ink_threshold):
                continue
            output_pixels[offset : offset + 3] = replacement
            toned_pixels += 1

    write_ppm(output, width, height, bytes(output_pixels))
    return {
        "line_count": len(bands),
        "target_line": target_band_index + 1,
        "top": top,
        "bottom": bottom,
        "region_left": region_left,
        "region_right": region_right,
        "width_fraction": width_fraction,
        "x_position": x_position,
        "tone": tone,
        "toned_ink_pixels": toned_pixels,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--width-fraction", type=float, default=0.10)
    parser.add_argument("--x-position", type=float, default=0.40)
    parser.add_argument("--tone", type=int, default=160)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    return parser.parse_args()


def fraction_label(value: float) -> str:
    return f"{value:.3f}".replace(".", "p")


def position_label(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def main() -> int:
    args = parse_args()
    if not 0.0 < args.width_fraction <= 1.0:
        raise RuntimeError("--width-fraction must be in (0, 1]")
    if not 0.0 <= args.x_position <= 1.0:
        raise RuntimeError("--x-position must be in [0, 1]")
    if not 0 <= args.tone < args.ink_threshold <= 255:
        raise RuntimeError("--tone must preserve the ink mask: 0 <= tone < ink-threshold <= 255")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, args.reference_prefix)
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    width_label = fraction_label(args.width_fraction)
    x_label = position_label(args.x_position)
    variant = f"sensitivity-tone-w{width_label}-xp{x_label}-v{args.tone}"
    candidate_prefix = f"system-harfbuzz-tone-w{width_label}-xp{x_label}-v{args.tone}"
    output_dir = directory / variant

    pages: list[dict[str, int | float]] = []
    for page_number, source in enumerate(reference_pages, start=1):
        output = output_dir / f"{candidate_prefix}-{page_number}.ppm"
        pages.append(
            {
                "page": page_number,
                **distort_line_tone(
                    source,
                    output,
                    width_fraction=args.width_fraction,
                    x_position=args.x_position,
                    tone=args.tone,
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
        "tone": args.tone,
        "ink_threshold": args.ink_threshold,
        "max_blank_row_gap": args.max_blank_row_gap,
        "pages": pages,
    }
    output_path = directory / f"sensitivity-results-tone-w{width_label}-xp{x_label}-v{args.tone}.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
