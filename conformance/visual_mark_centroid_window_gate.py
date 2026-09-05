#!/usr/bin/env python3
"""Gate localized Khmer mark placement using ink centroids in upper/lower zones.

Profile metrics catch distribution changes, but a compact mark can translate while retaining
much of its internal shape. This gate measures the centroid of rendered ink inside the same
overlapping mark-zone windows and limits backend-specific displacement in raster pixels.
"""

from __future__ import annotations

import argparse
import json
from math import hypot
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_vertical_zone_window_gate import zone_bounds


def ink_centroid(
    pixels: bytes,
    width: int,
    top: int,
    bottom: int,
    left: int,
    right: int,
    ink_threshold: int,
) -> tuple[float, float, int] | None:
    sx = 0.0
    sy = 0.0
    count = 0
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            offset = (y * width + x) * 3
            if pixel_is_ink(pixels, offset, ink_threshold):
                sx += x
                sy += y
                count += 1
    if count == 0:
        return None
    return sx / count, sy / count, count


def compare_page(
    reference_path: Path,
    candidate_path: Path,
    *,
    zone: str,
    zone_fraction: float,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    min_zone_ink_pixels: int,
    max_blank_row_gap: int,
) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    candidate_width, candidate_height, candidate = read_ppm(candidate_path)
    if (width, height) != (candidate_width, candidate_height):
        raise RuntimeError(
            "render dimensions differ: "
            f"reference={width}x{height}, candidate={candidate_width}x{candidate_height}"
        )

    bands = detect_ink_bands(
        width,
        height,
        reference,
        candidate,
        ink_threshold=ink_threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    windows: list[dict[str, object]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs: list[int] = []
        for y in range(top, bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if pixel_is_ink(reference, offset, ink_threshold) or pixel_is_ink(
                    candidate, offset, ink_threshold
                ):
                    xs.append(x)
        if not xs:
            continue

        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        window_width = max(1, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)

        zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            reference_centroid = ink_centroid(
                reference,
                width,
                zone_top,
                zone_bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            candidate_centroid = ink_centroid(
                candidate,
                width,
                zone_top,
                zone_bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            if reference_centroid is None or candidate_centroid is None:
                continue
            rx, ry, reference_ink = reference_centroid
            cx, cy, candidate_ink = candidate_centroid
            if max(reference_ink, candidate_ink) < min_zone_ink_pixels:
                continue
            dx = cx - rx
            dy = cy - ry
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "right": window_right,
                    "zone_top": zone_top,
                    "zone_bottom": zone_bottom,
                    "reference_zone_ink_pixels": reference_ink,
                    "candidate_zone_ink_pixels": candidate_ink,
                    "reference_centroid": [rx, ry],
                    "candidate_centroid": [cx, cy],
                    "centroid_dx_pixels": dx,
                    "centroid_dy_pixels": dy,
                    "centroid_displacement_pixels": hypot(dx, dy),
                }
            )

    if not windows:
        raise RuntimeError("no mark-zone window met the minimum ink threshold")

    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "maximum_centroid_displacement_pixels": max(
            float(window["centroid_displacement_pixels"]) for window in windows
        ),
        "windows": windows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--zone", choices=("upper", "lower"), default="upper")
    parser.add_argument("--zone-fraction", type=float, default=0.25)
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-zone-ink-pixels", type=int, default=8)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--max-centroid-displacement", type=float, default=0.50)
    parser.add_argument("--expect-above", type=float, default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.zone_fraction <= 0.5:
        raise RuntimeError("--zone-fraction must be in (0, 0.5]")
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if args.min_zone_ink_pixels <= 0:
        raise RuntimeError("--min-zone-ink-pixels must be positive")
    if args.max_centroid_displacement < 0.0:
        raise RuntimeError("--max-centroid-displacement must be non-negative")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, args.reference_prefix)
    candidate_pages = numbered_pages(directory, args.candidate_prefix)
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")
    if len(reference_pages) != len(candidate_pages):
        raise RuntimeError(
            "rendered page counts differ: "
            f"reference={len(reference_pages)}, candidate={len(candidate_pages)}"
        )

    pages: list[dict[str, object]] = []
    for page_number, (reference, candidate) in enumerate(
        zip(reference_pages, candidate_pages), start=1
    ):
        pages.append(
            {
                "page": page_number,
                **compare_page(
                    reference,
                    candidate,
                    zone=args.zone,
                    zone_fraction=args.zone_fraction,
                    window_fraction=args.window_fraction,
                    stride_fraction=args.stride_fraction,
                    ink_threshold=args.ink_threshold,
                    min_zone_ink_pixels=args.min_zone_ink_pixels,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    maximum_displacement = max(
        float(page["maximum_centroid_displacement_pixels"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "min_zone_ink_pixels": args.min_zone_ink_pixels,
        "maximum_centroid_displacement_pixels": maximum_displacement,
        "pages": pages,
    }
    calibration = args.expect_above is not None
    output = Path(args.output) if args.output else directory / (
        "mark-centroid-window-calibration.json" if calibration else "mark-centroid-window-results.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if calibration:
        if maximum_displacement <= args.expect_above:
            print(
                "localized mark centroid calibration failed to detect deliberate mark shift: "
                f"maximum={maximum_displacement:.6f}px, expected > {args.expect_above:.6f}px",
                file=sys.stderr,
            )
            return 1
        return 0

    if maximum_displacement > args.max_centroid_displacement:
        print(
            f"maximum localized mark centroid displacement {maximum_displacement:.6f}px exceeds "
            f"allowed {args.max_centroid_displacement:.6f}px",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
