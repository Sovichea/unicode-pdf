#!/usr/bin/env python3
"""Gate localized Khmer mark shape using centroid-relative ink spread.

Centroid and row/column profiles can miss a compact mark that deforms symmetrically around
its center. This gate measures RMS ink spread around each mark-zone centroid inside the same
overlapping upper/lower windows and limits backend-specific shape change in raster pixels.
"""

from __future__ import annotations

import argparse
import json
from math import sqrt
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_vertical_zone_window_gate import zone_bounds


def ink_moments(
    pixels: bytes,
    width: int,
    top: int,
    bottom: int,
    left: int,
    right: int,
    ink_threshold: int,
) -> tuple[float, float, float, float, int] | None:
    points: list[tuple[int, int]] = []
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            offset = (y * width + x) * 3
            if pixel_is_ink(pixels, offset, ink_threshold):
                points.append((x, y))
    if not points:
        return None

    count = len(points)
    cx = sum(x for x, _ in points) / count
    cy = sum(y for _, y in points) / count
    rms_x = sqrt(sum((x - cx) ** 2 for x, _ in points) / count)
    rms_y = sqrt(sum((y - cy) ** 2 for _, y in points) / count)
    return cx, cy, rms_x, rms_y, count


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
            reference_moments = ink_moments(
                reference, width, zone_top, zone_bottom, window_left, window_right, ink_threshold
            )
            candidate_moments = ink_moments(
                candidate, width, zone_top, zone_bottom, window_left, window_right, ink_threshold
            )
            if reference_moments is None or candidate_moments is None:
                continue
            rcx, rcy, rrms_x, rrms_y, reference_ink = reference_moments
            ccx, ccy, crms_x, crms_y, candidate_ink = candidate_moments
            if max(reference_ink, candidate_ink) < min_zone_ink_pixels:
                continue
            dx = crms_x - rrms_x
            dy = crms_y - rrms_y
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
                    "reference_centroid": [rcx, rcy],
                    "candidate_centroid": [ccx, ccy],
                    "reference_rms_spread_pixels": [rrms_x, rrms_y],
                    "candidate_rms_spread_pixels": [crms_x, crms_y],
                    "rms_spread_dx_pixels": dx,
                    "rms_spread_dy_pixels": dy,
                    "rms_spread_change_pixels": sqrt(dx * dx + dy * dy),
                }
            )

    if not windows:
        raise RuntimeError("no mark-zone window met the minimum ink threshold")

    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "maximum_rms_spread_change_pixels": max(
            float(window["rms_spread_change_pixels"]) for window in windows
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
    parser.add_argument("--max-rms-spread-change", type=float, default=0.50)
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
    if args.max_rms_spread_change < 0.0:
        raise RuntimeError("--max-rms-spread-change must be non-negative")

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
    for page_number, (reference, candidate) in enumerate(zip(reference_pages, candidate_pages), start=1):
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

    maximum_change = max(float(page["maximum_rms_spread_change_pixels"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "min_zone_ink_pixels": args.min_zone_ink_pixels,
        "maximum_rms_spread_change_pixels": maximum_change,
        "pages": pages,
    }
    calibration = args.expect_above is not None
    output = Path(args.output) if args.output else directory / (
        "mark-spread-window-calibration.json" if calibration else "mark-spread-window-results.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if calibration:
        if maximum_change <= args.expect_above:
            print(
                "localized mark spread calibration failed to detect deliberate shape change: "
                f"maximum={maximum_change:.6f}px, expected > {args.expect_above:.6f}px",
                file=sys.stderr,
            )
            return 1
        return 0

    if maximum_change > args.max_rms_spread_change:
        print(
            f"maximum localized mark RMS spread change {maximum_change:.6f}px exceeds "
            f"allowed {args.max_rms_spread_change:.6f}px",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
