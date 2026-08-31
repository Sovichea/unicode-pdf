#!/usr/bin/env python3
"""Check renderer disagreement is backend-invariant in localized Khmer windows.

Whole-page renderer-consistency summaries can hide a backend-specific discrepancy in
a small glyph neighborhood. This gate compares Poppler-versus-MuPDF disagreement
inside the same overlapping line windows for the reference and candidate shaping
backends, then limits the maximum per-window metric delta.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_sliding_window_gate import compare_windows


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    matches = sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )
    if not matches:
        raise RuntimeError(f"no raster pages found for {directory / prefix}")
    return matches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-window-ink-pixels", type=int, default=32)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--max-window-metric-delta", type=float, default=0.001)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def renderer_windows(
    poppler: Path,
    mutool: Path,
    args: argparse.Namespace,
) -> dict[str, object]:
    return compare_windows(
        poppler,
        mutool,
        window_fraction=args.window_fraction,
        stride_fraction=args.stride_fraction,
        ink_threshold=args.ink_threshold,
        min_window_ink_pixels=args.min_window_ink_pixels,
        max_blank_row_gap=args.max_blank_row_gap,
    )


def window_key(window: dict[str, object]) -> tuple[int, int, int, int, int]:
    return (
        int(window["line"]),
        int(window["left"]),
        int(window["top"]),
        int(window["right"]),
        int(window["bottom"]),
    )


def main() -> int:
    args = parse_args()
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if args.max_window_metric_delta < 0.0:
        raise RuntimeError("--max-window-metric-delta must be non-negative")

    directory = args.dir
    mutool_dir = directory / f"mutool-{args.dpi}dpi"
    if not mutool_dir.is_dir():
        raise RuntimeError(
            f"missing MuPDF raster directory {mutool_dir}; run visual_cross_renderer_gate.py first"
        )

    backend_pages: dict[str, list[dict[str, object]]] = {}
    for prefix in (args.reference_prefix, args.candidate_prefix):
        poppler_pages = numbered_pages(directory, prefix)
        mutool_pages = numbered_pages(mutool_dir, prefix)
        if len(poppler_pages) != len(mutool_pages):
            raise RuntimeError(
                f"renderer page counts differ for {prefix}: "
                f"poppler={len(poppler_pages)}, mutool={len(mutool_pages)}"
            )
        backend_pages[prefix] = [
            renderer_windows(poppler, mutool, args)
            for poppler, mutool in zip(poppler_pages, mutool_pages)
        ]

    reference_pages = backend_pages[args.reference_prefix]
    candidate_pages = backend_pages[args.candidate_prefix]
    if len(reference_pages) != len(candidate_pages):
        raise RuntimeError(
            "backend page counts differ: "
            f"reference={len(reference_pages)}, candidate={len(candidate_pages)}"
        )

    page_results: list[dict[str, object]] = []
    maximum_delta = 0.0
    compared_windows = 0
    for page_number, (reference, candidate) in enumerate(
        zip(reference_pages, candidate_pages), start=1
    ):
        reference_by_key = {
            window_key(window): window
            for window in reference["windows"]
        }
        candidate_by_key = {
            window_key(window): window
            for window in candidate["windows"]
        }
        if reference_by_key.keys() != candidate_by_key.keys():
            missing_from_candidate = sorted(reference_by_key.keys() - candidate_by_key.keys())
            missing_from_reference = sorted(candidate_by_key.keys() - reference_by_key.keys())
            raise RuntimeError(
                f"renderer window geometry differs between backends on page {page_number}: "
                f"missing_from_candidate={missing_from_candidate[:5]}, "
                f"missing_from_reference={missing_from_reference[:5]}"
            )

        windows: list[dict[str, object]] = []
        for key in sorted(reference_by_key):
            reference_window = reference_by_key[key]
            candidate_window = candidate_by_key[key]
            iou_delta = abs(
                float(reference_window["ink_iou"]) - float(candidate_window["ink_iou"])
            )
            similarity_delta = abs(
                float(reference_window["ink_similarity"])
                - float(candidate_window["ink_similarity"])
            )
            window_delta = max(iou_delta, similarity_delta)
            maximum_delta = max(maximum_delta, window_delta)
            compared_windows += 1
            windows.append(
                {
                    "line": key[0],
                    "left": key[1],
                    "top": key[2],
                    "right": key[3],
                    "bottom": key[4],
                    "reference_renderer_ink_iou": reference_window["ink_iou"],
                    "candidate_renderer_ink_iou": candidate_window["ink_iou"],
                    "ink_iou_delta": iou_delta,
                    "reference_renderer_ink_similarity": reference_window["ink_similarity"],
                    "candidate_renderer_ink_similarity": candidate_window["ink_similarity"],
                    "ink_similarity_delta": similarity_delta,
                    "maximum_metric_delta": window_delta,
                }
            )
        page_results.append({"page": page_number, "windows": windows})

    result = {
        "comparison": "localized Poppler-versus-MuPDF disagreement by shaping backend",
        "dpi": args.dpi,
        "ink_threshold": args.ink_threshold,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "max_window_metric_delta": args.max_window_metric_delta,
        "compared_windows": compared_windows,
        "maximum_window_metric_delta": maximum_delta,
        "pages": page_results,
    }
    output = args.output or directory / f"renderer-window-consistency-{args.dpi}dpi.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if maximum_delta > args.max_window_metric_delta:
        print(
            "localized renderer consistency gate failed: maximum backend-specific "
            f"window metric delta {maximum_delta:.9f} > "
            f"{args.max_window_metric_delta:.9f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
