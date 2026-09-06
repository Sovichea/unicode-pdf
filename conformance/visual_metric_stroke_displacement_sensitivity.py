#!/usr/bin/env python3
"""Create a localized Khmer stroke-displacement defect for visual calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def displace_region(source: Path, output: Path, *, width_fraction: float, x_position: float, dx: int, ink_threshold: int, max_blank_row_gap: int) -> dict[str, int | float]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(width, height, pixels, pixels, ink_threshold=ink_threshold, max_blank_row_gap=max_blank_row_gap)
    if not bands:
        raise RuntimeError(f"no rendered-ink band found in {source}")
    target = len(bands) // 2
    top, bottom = bands[target]
    xs = [x for y in range(top, bottom + 1) for x in range(width) if pixel_is_ink(pixels, (y * width + x) * 3, ink_threshold)]
    if not xs:
        raise RuntimeError("selected ink band is empty")
    left, right = min(xs), max(xs)
    ink_width = right - left + 1
    region_width = max(3, round(ink_width * width_fraction))
    region_left = left + round((ink_width - region_width) * x_position)
    region_right = min(right, region_left + region_width - 1)
    if region_left + dx < 0 or region_right + dx >= width:
        raise RuntimeError("displacement would leave raster")

    current = bytearray(pixels)
    moved: list[tuple[int, int, bytes]] = []
    for y in range(top, bottom + 1):
        for x in range(region_left, region_right + 1):
            offset = (y * width + x) * 3
            if pixel_is_ink(pixels, offset, ink_threshold):
                moved.append((x, y, pixels[offset : offset + 3]))
                current[offset : offset + 3] = b"\xff\xff\xff"
    for x, y, rgb in moved:
        dst = (y * width + x + dx) * 3
        current[dst : dst + 3] = rgb
    if not moved:
        raise RuntimeError("displacement mutation moved no ink pixels")
    write_ppm(output, width, height, bytes(current))
    return {"line_count": len(bands), "target_line": target + 1, "top": top, "bottom": bottom, "region_left": region_left, "region_right": region_right, "width_fraction": width_fraction, "x_position": x_position, "dx": dx, "moved_pixels": len(moved)}


def label(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", "p")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--width-fraction", type=float, default=0.10)
    parser.add_argument("--x-position", type=float, default=0.40)
    parser.add_argument("--dx", type=int, default=2)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    args = parser.parse_args()
    if not 0.0 < args.width_fraction <= 1.0 or not 0.0 <= args.x_position <= 1.0 or args.dx == 0:
        raise RuntimeError("invalid displacement parameters")

    directory = Path(args.dir)
    pages = numbered_pages(directory, args.reference_prefix)
    if not pages:
        raise RuntimeError(f"no reference rasters found in {directory}")
    wl, xp = label(args.width_fraction, 3), label(args.x_position, 2)
    variant = f"sensitivity-stroke-displacement-w{wl}-xp{xp}-dx{args.dx}"
    prefix = f"system-harfbuzz-stroke-displacement-w{wl}-xp{xp}-dx{args.dx}"
    results = []
    for number, source in enumerate(pages, start=1):
        output = directory / variant / f"{prefix}-{number}.ppm"
        results.append({"page": number, **displace_region(source, output, width_fraction=args.width_fraction, x_position=args.x_position, dx=args.dx, ink_threshold=args.ink_threshold, max_blank_row_gap=args.max_blank_row_gap)})
    result = {"reference_prefix": args.reference_prefix, "candidate_prefix": f"{variant}/{prefix}", "pages": results}
    (directory / f"sensitivity-results-stroke-displacement-w{wl}-xp{xp}-dx{args.dx}.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
