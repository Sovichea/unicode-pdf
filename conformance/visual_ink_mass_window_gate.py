#!/usr/bin/env python3
"""Gate localized Khmer grayscale ink mass in overlapping line windows.

Binary masks can remain unchanged while stroke weight or antialias density changes
visibly. This gate sums continuous pixel darkness rather than thresholded ink so
local bold/thin or tone regressions cannot hide behind stable topology, counters,
or scanline-transition counts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def pixel_darkness(pixels: bytes, offset: int) -> float:
    return 255.0 - (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) / 3.0


def mass_similarity(reference_mass: float, candidate_mass: float) -> float:
    maximum = max(reference_mass, candidate_mass)
    if maximum <= 0.0:
        return 1.0
    return min(reference_mass, candidate_mass) / maximum


def compare_page(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    max_blank_row_gap: int,
) -> dict[str, object]:
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
        window_width = max(2, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)

        for window_index, wl in enumerate(starts, start=1):
            wr = min(right, wl + window_width - 1)
            reference_mass = 0.0
            candidate_mass = 0.0
            for y in range(top, bottom + 1):
                for x in range(wl, wr + 1):
                    offset = (y * width + x) * 3
                    reference_mass += pixel_darkness(reference, offset)
                    candidate_mass += pixel_darkness(candidate, offset)
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": wl,
                    "right": wr,
                    "reference_ink_mass": reference_mass,
                    "candidate_ink_mass": candidate_mass,
                    "ink_mass_similarity": mass_similarity(reference_mass, candidate_mass),
                }
            )

    if not windows:
        raise RuntimeError("no nonblank line windows were measured")
    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_ink_mass_similarity": min(
            float(window["ink_mass_similarity"]) for window in windows
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
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float)
    parser.add_argument("--output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.stride_fraction <= args.window_fraction <= 1.0:
        raise RuntimeError("invalid window configuration")

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
                window_fraction=args.window_fraction,
                stride_fraction=args.stride_fraction,
                ink_threshold=args.ink_threshold,
                max_blank_row_gap=args.max_blank_row_gap,
            ),
        }
        for page_number, (reference, candidate) in enumerate(
            zip(references, candidates), start=1
        )
    ]
    minimum = min(float(page["minimum_ink_mass_similarity"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "minimum_ink_mass_similarity": minimum,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "ink-mass-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum >= args.expect_below:
            print(
                f"ink-mass calibration failed: minimum={minimum:.10f}, expected < {args.expect_below}",
                file=sys.stderr,
            )
            return 1
        return 0
    if minimum < args.min_similarity:
        print(
            f"minimum localized ink-mass similarity {minimum:.10f} is below required {args.min_similarity:.10f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
