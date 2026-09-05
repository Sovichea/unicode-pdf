#!/usr/bin/env python3
"""Gate localized directional grayscale halo fidelity around Khmer stroke cores."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def gray(data: bytes, offset: int) -> float:
    return (data[offset] + data[offset + 1] + data[offset + 2]) / 3.0


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def compare_windows(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    core_threshold: int,
    radius: int,
    min_halo_pixels: int,
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
        ink_threshold=250,
        max_blank_row_gap=max_blank_row_gap,
    )
    windows: list[dict[str, int | float]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        core: set[tuple[int, int]] = set()
        for y in range(top, bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if pixel_is_ink(reference, offset, core_threshold) or pixel_is_ink(
                    candidate, offset, core_threshold
                ):
                    core.add((x, y))
        if not core:
            continue

        xs = [x for x, _ in core]
        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        window_width = max(1, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)

        halo: set[tuple[int, int]] = set()
        for x, y in core:
            for dy in range(-radius, radius + 1):
                yy = y + dy
                if yy < 0 or yy >= height:
                    continue
                for dx in range(-radius, radius + 1):
                    if dx == 0 and dy == 0:
                        continue
                    xx = x + dx
                    if 0 <= xx < width and (xx, yy) not in core:
                        halo.add((xx, yy))

        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            points = [
                (x, y)
                for x, y in halo
                if window_left <= x <= window_right
                and max(0, top - radius) <= y <= min(height - 1, bottom + radius)
            ]
            if len(points) < min_halo_pixels:
                continue

            errors: list[float] = []
            for x, y in points:
                offset = (y * width + x) * 3
                errors.append(abs(gray(reference, offset) - gray(candidate, offset)))
            p90_error = percentile(errors, 0.90)
            max_error = max(errors)
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "top": max(0, top - radius),
                    "right": window_right,
                    "bottom": min(height - 1, bottom + radius),
                    "halo_pixels": len(points),
                    "p90_error": p90_error,
                    "max_error": max_error,
                    "similarity": 1.0 - p90_error / 255.0,
                }
            )

    if not windows:
        raise RuntimeError("no halo window met the minimum support threshold")

    return {
        "line_count": len(bands),
        "windows": windows,
        "minimum_similarity": min(float(window["similarity"]) for window in windows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--window-fraction", type=float, default=0.04)
    parser.add_argument("--stride-fraction", type=float, default=0.02)
    parser.add_argument("--core-threshold", type=int, default=200)
    parser.add_argument("--radius", type=int, default=2)
    parser.add_argument("--min-halo-pixels", type=int, default=24)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if not 0 <= args.core_threshold <= 255:
        raise RuntimeError("--core-threshold must be in [0, 255]")
    if args.radius < 1:
        raise RuntimeError("--radius must be >= 1")
    if args.min_halo_pixels <= 0:
        raise RuntimeError("--min-halo-pixels must be positive")

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
                    core_threshold=args.core_threshold,
                    radius=args.radius,
                    min_halo_pixels=args.min_halo_pixels,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    minimum_similarity = min(float(page["minimum_similarity"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "core_threshold": args.core_threshold,
        "radius": args.radius,
        "min_halo_pixels": args.min_halo_pixels,
        "minimum_similarity": minimum_similarity,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "halo-balance-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_similarity >= args.expect_below:
            print(
                "halo-balance calibration failed to detect deliberate asymmetry: "
                f"minimum_similarity={minimum_similarity:.6f}, expected < "
                f"{args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_similarity < args.min_similarity:
        print(
            f"minimum halo-balance similarity {minimum_similarity:.6f} is below "
            f"required {args.min_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
