#!/usr/bin/env python3
"""Gate localized Khmer above/below-base mark placement inside vertical zones.

The whole-window vertical-profile gate can still include substantial base-glyph ink.
This gate restricts the row-wise profile to a configurable top or bottom fraction of
each detected text line, then evaluates overlapping horizontal windows. It is aimed
at Khmer vowel signs and diacritics that occupy the upper or lower line zones.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def normalized_zone_profile(
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


def zone_bounds(top: int, bottom: int, zone: str, zone_fraction: float) -> tuple[int, int]:
    height = bottom - top + 1
    zone_height = max(1, round(height * zone_fraction))
    if zone == "upper":
        return top, min(bottom, top + zone_height - 1)
    return max(top, bottom - zone_height + 1), bottom


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
            reference_profile, reference_ink = normalized_zone_profile(
                reference,
                width,
                zone_top,
                zone_bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            candidate_profile, candidate_ink = normalized_zone_profile(
                candidate,
                width,
                zone_top,
                zone_bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            if max(reference_ink, candidate_ink) < min_zone_ink_pixels:
                continue
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
                    "profile_similarity": profile_similarity(reference_profile, candidate_profile),
                    "reference_profile": reference_profile,
                    "candidate_profile": candidate_profile,
                }
            )

    if not windows:
        raise RuntimeError("no mark-zone window met the minimum ink threshold")

    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_zone_profile_similarity": min(
            float(window["profile_similarity"]) for window in windows
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
    parser.add_argument("--min-zone-profile-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
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

    minimum_similarity = min(float(page["minimum_zone_profile_similarity"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "min_zone_ink_pixels": args.min_zone_ink_pixels,
        "minimum_zone_profile_similarity": minimum_similarity,
        "pages": pages,
    }
    calibration = args.expect_below is not None
    output = Path(args.output) if args.output else directory / (
        "vertical-zone-window-calibration.json" if calibration else "vertical-zone-window-results.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if calibration:
        if minimum_similarity >= args.expect_below:
            print(
                "localized mark-zone calibration failed to detect deliberate mark shift: "
                f"minimum={minimum_similarity:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_similarity < args.min_zone_profile_similarity:
        print(
            f"minimum local mark-zone profile similarity {minimum_similarity:.6f} is below "
            f"required {args.min_zone_profile_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
