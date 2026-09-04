#!/usr/bin/env python3
"""Gate localized Khmer stroke-width structure using horizontal and vertical run lengths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def run_histogram(
    pixels: bytes,
    width: int,
    *,
    top: int,
    bottom: int,
    left: int,
    right: int,
    ink_threshold: int,
    max_run: int,
) -> tuple[list[int], int]:
    histogram = [0] * max_run
    run_count = 0

    for y in range(top, bottom + 1):
        x = left
        while x <= right:
            offset = (y * width + x) * 3
            if not pixel_is_ink(pixels, offset, ink_threshold):
                x += 1
                continue
            end = x + 1
            while end <= right and pixel_is_ink(
                pixels, (y * width + end) * 3, ink_threshold
            ):
                end += 1
            length = min(max_run, end - x)
            histogram[length - 1] += 1
            run_count += 1
            x = end

    for x in range(left, right + 1):
        y = top
        while y <= bottom:
            offset = (y * width + x) * 3
            if not pixel_is_ink(pixels, offset, ink_threshold):
                y += 1
                continue
            end = y + 1
            while end <= bottom and pixel_is_ink(
                pixels, (end * width + x) * 3, ink_threshold
            ):
                end += 1
            length = min(max_run, end - y)
            histogram[length - 1] += 1
            run_count += 1
            y = end

    return histogram, run_count


def histogram_similarity(reference: list[int], candidate: list[int]) -> float:
    reference_total = sum(reference)
    candidate_total = sum(candidate)
    if reference_total == 0 or candidate_total == 0:
        raise RuntimeError("cannot compare empty stroke-width histogram")
    distance = 0.0
    for reference_count, candidate_count in zip(reference, candidate):
        reference_fraction = reference_count / reference_total
        candidate_fraction = candidate_count / candidate_total
        distance += abs(reference_fraction - candidate_fraction)
    return 1.0 - 0.5 * distance


def compare_windows(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    max_run: int,
    min_runs: int,
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
    windows: list[dict[str, int | float | list[int]]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs = [
            x
            for y in range(top, bottom + 1)
            for x in range(width)
            if pixel_is_ink(reference, (y * width + x) * 3, ink_threshold)
            or pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)
        ]
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

        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            reference_histogram, reference_runs = run_histogram(
                reference,
                width,
                top=top,
                bottom=bottom,
                left=window_left,
                right=window_right,
                ink_threshold=ink_threshold,
                max_run=max_run,
            )
            candidate_histogram, candidate_runs = run_histogram(
                candidate,
                width,
                top=top,
                bottom=bottom,
                left=window_left,
                right=window_right,
                ink_threshold=ink_threshold,
                max_run=max_run,
            )
            if min(reference_runs, candidate_runs) < min_runs:
                continue
            similarity = histogram_similarity(reference_histogram, candidate_histogram)
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "top": top,
                    "right": window_right,
                    "bottom": bottom,
                    "reference_runs": reference_runs,
                    "candidate_runs": candidate_runs,
                    "reference_histogram": reference_histogram,
                    "candidate_histogram": candidate_histogram,
                    "stroke_width_similarity": similarity,
                }
            )

    if not windows:
        raise RuntimeError("no stroke-width window met the minimum run threshold")

    return {
        "line_count": len(bands),
        "windows": windows,
        "minimum_stroke_width_similarity": min(
            float(window["stroke_width_similarity"]) for window in windows
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
    parser.add_argument("--max-run", type=int, default=20)
    parser.add_argument("--min-runs", type=int, default=20)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument(
        "--expect-below",
        type=float,
        default=None,
        help="Calibration mode: require stroke-width similarity below this value.",
    )
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if args.max_run < 1:
        raise RuntimeError("--max-run must be positive")
    if args.min_runs < 1:
        raise RuntimeError("--min-runs must be positive")

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
                    max_run=args.max_run,
                    min_runs=args.min_runs,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    minimum_similarity = min(
        float(page["minimum_stroke_width_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "max_run": args.max_run,
        "min_runs": args.min_runs,
        "minimum_stroke_width_similarity": minimum_similarity,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "stroke-width-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_similarity >= args.expect_below:
            print(
                "stroke-width calibration failed to detect deliberate thickening: "
                f"minimum_stroke_width_similarity={minimum_similarity:.6f}, "
                f"expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_similarity < args.min_similarity:
        print(
            "minimum stroke-width similarity "
            f"{minimum_similarity:.6f} is below required {args.min_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
