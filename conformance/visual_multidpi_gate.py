#!/usr/bin/env python3
"""Re-rasterize existing Khmer parity PDFs across multiple DPIs and gate fidelity.

The primary parity harness emits the two backend PDFs once at 144 DPI. This
companion check reuses those exact PDFs and rasterizes them at additional reader-
like scales so scale-dependent Khmer mark, hinting, or geometry regressions are
not hidden by a single raster resolution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import compare_page_sets, render_pages


def parse_dpis(value: str) -> list[int]:
    dpis: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        dpi = int(token)
        if dpi <= 0:
            raise argparse.ArgumentTypeError("DPIs must be positive integers")
        if dpi not in dpis:
            dpis.append(dpi)
    if not dpis:
        raise argparse.ArgumentTypeError("at least one DPI is required")
    return dpis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--dpis", type=parse_dpis, default=parse_dpis("96,288"))
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--min-similarity", type=float, default=0.995)
    parser.add_argument("--min-ink-iou", type=float, default=0.90)
    parser.add_argument("--min-ink-similarity", type=float, default=0.90)
    parser.add_argument("--ink-threshold", type=int, default=250)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 <= args.ink_threshold <= 255:
        raise RuntimeError("--ink-threshold must be between 0 and 255")

    directory = Path(args.dir)
    reference_pdf = directory / f"{args.reference_prefix}.pdf"
    candidate_pdf = directory / f"{args.candidate_prefix}.pdf"
    if not reference_pdf.is_file():
        raise RuntimeError(f"reference PDF does not exist: {reference_pdf}")
    if not candidate_pdf.is_file():
        raise RuntimeError(f"candidate PDF does not exist: {candidate_pdf}")

    scales: list[dict[str, object]] = []
    failed = False
    for dpi in args.dpis:
        scale_dir = directory / f"dpi-{dpi}"
        scale_dir.mkdir(parents=True, exist_ok=True)
        reference_pages = render_pages(
            reference_pdf, scale_dir / args.reference_prefix, dpi
        )
        candidate_pages = render_pages(
            candidate_pdf, scale_dir / args.candidate_prefix, dpi
        )
        pages, metrics = compare_page_sets(
            reference_pages, candidate_pages, ink_threshold=args.ink_threshold
        )
        scale_result = {"dpi": dpi, "pages": pages, **metrics}
        scales.append(scale_result)

        minimum_page_similarity = float(metrics["minimum_page_similarity"])
        minimum_page_ink_iou = float(metrics["minimum_page_ink_iou"])
        minimum_page_ink_similarity = float(metrics["minimum_page_ink_similarity"])
        if minimum_page_similarity < args.min_similarity:
            print(
                f"{dpi} DPI minimum page similarity {minimum_page_similarity:.6f} "
                f"is below required {args.min_similarity:.6f}",
                file=sys.stderr,
            )
            failed = True
        if minimum_page_ink_iou < args.min_ink_iou:
            print(
                f"{dpi} DPI minimum page ink IoU {minimum_page_ink_iou:.6f} "
                f"is below required {args.min_ink_iou:.6f}",
                file=sys.stderr,
            )
            failed = True
        if minimum_page_ink_similarity < args.min_ink_similarity:
            print(
                f"{dpi} DPI minimum page ink similarity "
                f"{minimum_page_ink_similarity:.6f} is below required "
                f"{args.min_ink_similarity:.6f}",
                file=sys.stderr,
            )
            failed = True

    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "dpis": args.dpis,
        "minimum_similarity": args.min_similarity,
        "minimum_ink_iou": args.min_ink_iou,
        "minimum_ink_similarity": args.min_ink_similarity,
        "ink_threshold": args.ink_threshold,
        "minimum_scale_page_similarity": min(
            float(scale["minimum_page_similarity"]) for scale in scales
        ),
        "minimum_scale_page_ink_iou": min(
            float(scale["minimum_page_ink_iou"]) for scale in scales
        ),
        "minimum_scale_page_ink_similarity": min(
            float(scale["minimum_page_ink_similarity"]) for scale in scales
        ),
        "scales": scales,
    }
    output = directory / "multidpi-results.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
