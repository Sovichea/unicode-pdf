#!/usr/bin/env python3
"""Gate Khmer line-local geometry across multiple rendered-ink thresholds.

A single binary ink cutoff can hide stroke-weight regressions when pixels remain
on the same side of that cutoff. This gate reuses the overlapping line-window
comparison at several thresholds so dark stroke cores, mid-tones, and
antialiased edges all have to preserve local occupancy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_line_gate import numbered_pages
from visual_sliding_window_gate import compare_windows


def parse_thresholds(value: str) -> list[int]:
    thresholds = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not thresholds:
        raise argparse.ArgumentTypeError("at least one ink threshold is required")
    if any(threshold <= 0 or threshold > 255 for threshold in thresholds):
        raise argparse.ArgumentTypeError("ink thresholds must be in [1, 255]")
    if len(set(thresholds)) != len(thresholds):
        raise argparse.ArgumentTypeError("ink thresholds must be unique")
    return thresholds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--ink-thresholds", type=parse_thresholds, default=parse_thresholds("64,128,192,250"))
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--min-window-ink-pixels", type=int, default=32)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-window-ink-iou", type=float, default=0.90)
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

    thresholds: list[dict[str, object]] = []
    for ink_threshold in args.ink_thresholds:
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
                        ink_threshold=ink_threshold,
                        min_window_ink_pixels=args.min_window_ink_pixels,
                        max_blank_row_gap=args.max_blank_row_gap,
                    ),
                }
            )
        thresholds.append(
            {
                "ink_threshold": ink_threshold,
                "minimum_window_ink_iou": min(
                    float(page["minimum_window_ink_iou"]) for page in pages
                ),
                "pages": pages,
            }
        )

    minimum_iou = min(
        float(threshold["minimum_window_ink_iou"]) for threshold in thresholds
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "ink_thresholds": args.ink_thresholds,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "min_window_ink_pixels": args.min_window_ink_pixels,
        "minimum_multithreshold_window_ink_iou": minimum_iou,
        "thresholds": thresholds,
    }
    output_name = (
        "multithreshold-calibration.json"
        if args.expect_below is not None
        else "multithreshold-results.json"
    )
    (directory / output_name).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_iou >= args.expect_below:
            print(
                "multi-threshold calibration failed to detect deliberate stroke distortion: "
                f"minimum_window_ink_iou={minimum_iou:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_iou < args.min_window_ink_iou:
        print(
            "minimum multi-threshold window ink IoU "
            f"{minimum_iou:.6f} is below required {args.min_window_ink_iou:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
