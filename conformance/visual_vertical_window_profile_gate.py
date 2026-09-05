#!/usr/bin/env python3
"""Gate Khmer vertical mark placement with overlapping local vertical profiles.

Whole-line row profiles can dilute a narrow vowel-sign or diacritic displacement.
This gate detects text lines, slides overlapping windows across each line's ink
extent, and compares the normalized row-wise ink distribution inside every
sufficiently populated window. A score of 1.0 is identical; 0.90 is the visual
fidelity floor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def normalized_window_profile(
    pixels: bytes,
    width: int,
    top: int,
    bottom: int,
    left: int,
    right: int,
    ink_threshold: int,
) -> tuple[list[float], int]:
    counts: list[int] = []
    for y in range(top, bottom + 1):
        count = 0
        for x in range(left, right + 1):
            offset = (y * width + x) * 3
            count += int(pixel_is_ink(pixels, offset, ink_threshold))
        counts.append(count)
    total = sum(counts)
    if total == 0:
        return [0.0 for _ in counts], 0
    return [count / total for count in counts], total


def profile_similarity(reference: list[float], candidate: list[float]) -> float:
    if len(reference) != len(candidate):
        raise RuntimeError("profile lengths differ")
    return 1.0 - 0.5 * sum(abs(a - b) for a, b in zip(reference, candidate))


def compare_page(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    min_window_ink_pixels: int,
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

        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            reference_profile, reference_ink = normalized_window_profile(
                reference,
                width,
                top,
                bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            candidate_profile, candidate_ink = normalized_window_profile(
                candidate,
                width,
                top,
                bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            if max(reference_ink, candidate_ink) < min_window_ink_pixels:
                continue
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "top": top,
                    "right": window_right,
                    "bottom": bottom,
                    "reference_ink_pixels": reference_ink,
                    "candidate_ink_pixels": candidate_ink,
                    "profile_similarity": profile_similarity(
                        reference_profile, candidate_profile
                    ),
                    "reference_profile": reference_profile,
                    "candidate_profile": candidate_profile,
                }
            )

    if not windows:
        raise RuntimeError("no vertical-profile window met the minimum ink threshold")

    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_window_profile_similarity": min(
            float(window["profile_similarity"]) for window in windows
        ),
        "windows": windows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-window-ink-pixels", type=int, default=32)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-window-profile-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if args.min_window_ink_pixels <= 0:
        raise RuntimeError("--min-window-ink-pixels must be positive")

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
                    window_fraction=args.window_fraction,
                    stride_fraction=args.stride_fraction,
                    ink_threshold=args.ink_threshold,
                    min_window_ink_pixels=args.min_window_ink_pixels,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    minimum_similarity = min(
        float(page["minimum_window_profile_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "min_window_ink_pixels": args.min_window_ink_pixels,
        "minimum_window_profile_similarity": minimum_similarity,
        "pages": pages,
    }
    calibration = args.expect_below is not None
    output = Path(args.output) if args.output else directory / (
        "vertical-window-profile-calibration.json"
        if calibration
        else "vertical-window-profile-results.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if calibration:
        if minimum_similarity >= args.expect_below:
            print(
                "localized vertical-profile calibration failed to detect deliberate mark shift: "
                f"minimum={minimum_similarity:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_similarity < args.min_window_profile_similarity:
        print(
            f"minimum local vertical-profile similarity {minimum_similarity:.6f} is below "
            f"required {args.min_window_profile_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
