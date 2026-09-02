#!/usr/bin/env python3
"""Gate localized Khmer mark ink-area preservation.

Connected-component counts can stay unchanged when a detached Khmer vowel sign or
other diacritic is partially eroded or thickened. This gate compares the number of
rendered ink pixels inside overlapping upper/lower mark-zone windows and rejects
backend-specific local area loss or gain even when topology is unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_vertical_zone_window_gate import zone_bounds


def ink_count(pixels: bytes, width: int, top: int, bottom: int, left: int, right: int,
              threshold: int) -> int:
    return sum(
        1
        for y in range(top, bottom + 1)
        for x in range(left, right + 1)
        if pixel_is_ink(pixels, (y * width + x) * 3, threshold)
    )


def area_similarity(reference_count: int, candidate_count: int) -> float:
    if reference_count == 0 and candidate_count == 0:
        return 1.0
    return min(reference_count, candidate_count) / max(reference_count, candidate_count)


def compare_page(reference_path: Path, candidate_path: Path, *, zone: str, zone_fraction: float,
                 window_fraction: float, stride_fraction: float, ink_threshold: int,
                 max_blank_row_gap: int) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    cw, ch, candidate = read_ppm(candidate_path)
    if (width, height) != (cw, ch):
        raise RuntimeError(
            f"render dimensions differ: reference={width}x{height}, candidate={cw}x{ch}"
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
        zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            reference_count = ink_count(
                reference,
                width,
                zone_top,
                zone_bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            candidate_count = ink_count(
                candidate,
                width,
                zone_top,
                zone_bottom,
                window_left,
                window_right,
                ink_threshold,
            )
            if reference_count == 0 and candidate_count == 0:
                continue
            similarity = area_similarity(reference_count, candidate_count)
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "right": window_right,
                    "zone_top": zone_top,
                    "zone_bottom": zone_bottom,
                    "reference_ink_pixels": reference_count,
                    "candidate_ink_pixels": candidate_count,
                    "ink_area_similarity": similarity,
                }
            )
    if not windows:
        raise RuntimeError("no nonblank mark-zone windows were measured")
    minimum = min(float(window["ink_area_similarity"]) for window in windows)
    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_ink_area_similarity": minimum,
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
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-ink-area-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.zone_fraction <= 0.5 or not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("zone/window fractions are out of range")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if not 0.0 <= args.min_ink_area_similarity <= 1.0:
        raise RuntimeError("--min-ink-area-similarity must be in [0, 1]")

    directory = Path(args.dir)
    references = numbered_pages(directory, args.reference_prefix)
    candidates = numbered_pages(directory, args.candidate_prefix)
    if not references or len(references) != len(candidates):
        raise RuntimeError(
            f"rendered page counts differ: reference={len(references)}, candidate={len(candidates)}"
        )

    pages = [
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
                max_blank_row_gap=args.max_blank_row_gap,
            ),
        }
        for page_number, (reference, candidate) in enumerate(zip(references, candidates), start=1)
    ]
    minimum = min(float(page["minimum_ink_area_similarity"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "minimum_ink_area_similarity": minimum,
        "pages": pages,
    }

    output = Path(args.output) if args.output else directory / "mark-area-window-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum >= args.expect_below:
            print(
                f"mark-area calibration failed: minimum={minimum:.10f}, expected < {args.expect_below}",
                file=sys.stderr,
            )
            return 1
        return 0
    if minimum < args.min_ink_area_similarity:
        print(
            f"minimum localized mark ink-area similarity {minimum:.10f} is below required "
            f"{args.min_ink_area_similarity:.10f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
