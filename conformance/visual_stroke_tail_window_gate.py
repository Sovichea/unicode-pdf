#!/usr/bin/env python3
"""Gate concentrated grayscale errors around localized Khmer stroke neighborhoods.

Mean and RMS neighborhood error can still dilute a small set of severe edge or
antialiasing mismatches. This companion gate measures high-percentile absolute
grayscale error inside the same line-local stroke neighborhoods. Requiring the
95th-percentile-derived similarity to stay at or above the visual target makes
concentrated high-contrast defects visible without replacing the existing
mean/RMS checks.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def gray(data: bytes, offset: int) -> float:
    return (data[offset] + data[offset + 1] + data[offset + 2]) / 3.0


def nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        raise RuntimeError("cannot compute percentile of an empty error set")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[min(len(ordered) - 1, rank - 1)]


def compare_windows(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    radius: int,
    min_support_pixels: int,
    max_blank_row_gap: int,
    quantile: float,
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
    windows: list[dict[str, int | float]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        ink_points: set[tuple[int, int]] = set()
        for y in range(top, bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if pixel_is_ink(reference, offset, ink_threshold) or pixel_is_ink(
                    candidate, offset, ink_threshold
                ):
                    ink_points.add((x, y))
        if not ink_points:
            continue

        xs = [x for x, _ in ink_points]
        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        window_width = max(1, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)

        support: set[tuple[int, int]] = set()
        for x, y in ink_points:
            for dy in range(-radius, radius + 1):
                yy = y + dy
                if yy < 0 or yy >= height:
                    continue
                for dx in range(-radius, radius + 1):
                    xx = x + dx
                    if 0 <= xx < width:
                        support.add((xx, yy))

        support_top = max(0, top - radius)
        support_bottom = min(height - 1, bottom + radius)
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            points = [
                (x, y)
                for x, y in support
                if window_left <= x <= window_right
                and support_top <= y <= support_bottom
            ]
            if len(points) < min_support_pixels:
                continue

            errors: list[float] = []
            for x, y in points:
                offset = (y * width + x) * 3
                errors.append(abs(gray(reference, offset) - gray(candidate, offset)))

            percentile_error = nearest_rank(errors, quantile)
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "top": support_top,
                    "right": window_right,
                    "bottom": support_bottom,
                    "support_pixels": len(points),
                    "quantile": quantile,
                    "percentile_absolute_error": percentile_error,
                    "percentile_similarity": 1.0 - percentile_error / 255.0,
                    "maximum_absolute_error": max(errors),
                }
            )

    if not windows:
        raise RuntimeError("no stroke-tail window met the minimum support threshold")

    return {
        "line_count": len(bands),
        "windows": windows,
        "minimum_percentile_similarity": min(
            float(window["percentile_similarity"]) for window in windows
        ),
        "maximum_percentile_absolute_error": max(
            float(window["percentile_absolute_error"]) for window in windows
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--radius", type=int, default=2)
    parser.add_argument("--min-support-pixels", type=int, default=48)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--quantile", type=float, default=0.95)
    parser.add_argument("--min-percentile-similarity", type=float, default=0.90)
    parser.add_argument(
        "--expect-below",
        type=float,
        default=None,
        help="Calibration mode: require percentile similarity below this value.",
    )
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if args.radius < 0:
        raise RuntimeError("--radius must be non-negative")
    if args.min_support_pixels <= 0:
        raise RuntimeError("--min-support-pixels must be positive")
    if not 0.0 < args.quantile <= 1.0:
        raise RuntimeError("--quantile must be in (0, 1]")

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
                **compare_windows(
                    reference,
                    candidate,
                    window_fraction=args.window_fraction,
                    stride_fraction=args.stride_fraction,
                    ink_threshold=args.ink_threshold,
                    radius=args.radius,
                    min_support_pixels=args.min_support_pixels,
                    max_blank_row_gap=args.max_blank_row_gap,
                    quantile=args.quantile,
                ),
            }
        )

    minimum_percentile_similarity = min(
        float(page["minimum_percentile_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "radius": args.radius,
        "min_support_pixels": args.min_support_pixels,
        "quantile": args.quantile,
        "minimum_percentile_similarity": minimum_percentile_similarity,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "stroke-tail-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_percentile_similarity >= args.expect_below:
            print(
                "stroke-tail calibration failed to detect deliberate distortion: "
                f"minimum_percentile_similarity={minimum_percentile_similarity:.6f}, "
                f"expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_percentile_similarity < args.min_percentile_similarity:
        print(
            "minimum stroke-tail percentile similarity "
            f"{minimum_percentile_similarity:.6f} is below required "
            f"{args.min_percentile_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
