#!/usr/bin/env python3
"""Gate Khmer visual parity with overlapping line-local windows.

Fixed line segments can dilute a defect that straddles a segment boundary. This
gate detects rendered-ink line bands, then evaluates overlapping windows across
each line so every localized Khmer mark or glyph neighborhood is tested in more
than one spatial alignment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def compare_windows(
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
    windows: list[dict[str, int | float]] = []
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
            intersection = 0
            union = 0
            absolute_error = 0
            for y in range(top, bottom + 1):
                for x in range(window_left, window_right + 1):
                    offset = (y * width + x) * 3
                    ref_ink = pixel_is_ink(reference, offset, ink_threshold)
                    cand_ink = pixel_is_ink(candidate, offset, ink_threshold)
                    intersection += int(ref_ink and cand_ink)
                    union += int(ref_ink or cand_ink)
                    if ref_ink or cand_ink:
                        absolute_error += sum(
                            abs(reference[offset + channel] - candidate[offset + channel])
                            for channel in range(3)
                        )

            if union < min_window_ink_pixels:
                continue
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "top": top,
                    "right": window_right,
                    "bottom": bottom,
                    "ink_union_pixels": union,
                    "ink_iou": intersection / union,
                    "ink_similarity": 1.0 - absolute_error / (union * 3 * 255),
                }
            )

    if not windows:
        raise RuntimeError("no sliding window met the minimum rendered-ink threshold")

    return {
        "line_count": len(bands),
        "windows": windows,
        "minimum_window_ink_iou": min(float(window["ink_iou"]) for window in windows),
        "minimum_window_ink_similarity": min(
            float(window["ink_similarity"]) for window in windows
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
    parser.add_argument("--min-window-ink-pixels", type=int, default=32)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-window-ink-iou", type=float, default=0.90)
    parser.add_argument("--min-window-ink-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
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
                **compare_windows(
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

    minimum_iou = min(float(page["minimum_window_ink_iou"]) for page in pages)
    minimum_similarity = min(
        float(page["minimum_window_ink_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "ink_threshold": args.ink_threshold,
        "min_window_ink_pixels": args.min_window_ink_pixels,
        "minimum_window_ink_iou": minimum_iou,
        "minimum_window_ink_similarity": minimum_similarity,
        "pages": pages,
    }
    output_name = "sliding-window-calibration.json" if args.expect_below is not None else "sliding-window-results.json"
    (directory / output_name).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_iou >= args.expect_below:
            print(
                "sliding-window calibration failed to detect deliberate distortion: "
                f"minimum_window_ink_iou={minimum_iou:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_iou < args.min_window_ink_iou:
        print(
            f"minimum window ink IoU {minimum_iou:.6f} is below required "
            f"{args.min_window_ink_iou:.6f}",
            file=sys.stderr,
        )
        return 1
    if minimum_similarity < args.min_window_ink_similarity:
        print(
            f"minimum window ink similarity {minimum_similarity:.6f} is below required "
            f"{args.min_window_ink_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
