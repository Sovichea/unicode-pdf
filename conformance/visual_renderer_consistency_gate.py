#!/usr/bin/env python3
"""Check that PDF rasterizer disagreement is backend-invariant for Khmer output.

Poppler and MuPDF do not rasterize antialiasing identically, so directly requiring
0.90 ink IoU between the two renderers is not a sound visual-accuracy gate. What
should remain stable is the amount of renderer disagreement for the HarfRust and
system-HarfBuzz PDFs. A backend-specific change in that disagreement can expose a
PDF construction or geometry difference even when each renderer separately shows
backend parity.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import compare_page_sets


def pages(directory: Path, prefix: str) -> list[Path]:
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
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--max-metric-delta", type=float, default=0.001)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def compare_renderer_pair(
    poppler_pages: list[Path], mutool_pages: list[Path], *, ink_threshold: int
) -> dict[str, object]:
    page_metrics, summary = compare_page_sets(
        poppler_pages, mutool_pages, ink_threshold=ink_threshold
    )
    return {"pages": page_metrics, "summary": summary}


def main() -> int:
    args = parse_args()
    directory = args.dir
    mutool_dir = directory / f"mutool-{args.dpi}dpi"
    if not mutool_dir.is_dir():
        raise RuntimeError(
            f"missing MuPDF raster directory {mutool_dir}; run visual_cross_renderer_gate.py first"
        )

    backends: dict[str, dict[str, object]] = {}
    for prefix in (args.reference_prefix, args.candidate_prefix):
        backends[prefix] = compare_renderer_pair(
            pages(directory, prefix),
            pages(mutool_dir, prefix),
            ink_threshold=args.ink_threshold,
        )

    metric_names = (
        "minimum_page_similarity",
        "minimum_page_ink_iou",
        "minimum_page_ink_similarity",
    )
    ref_summary = backends[args.reference_prefix]["summary"]
    cand_summary = backends[args.candidate_prefix]["summary"]
    assert isinstance(ref_summary, dict)
    assert isinstance(cand_summary, dict)

    deltas = {
        metric: abs(float(ref_summary[metric]) - float(cand_summary[metric]))
        for metric in metric_names
    }
    maximum_delta = max(deltas.values())

    result = {
        "comparison": "Poppler pdftoppm versus MuPDF mutool draw",
        "dpi": args.dpi,
        "ink_threshold": args.ink_threshold,
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "max_metric_delta": args.max_metric_delta,
        "metric_deltas": deltas,
        "maximum_metric_delta": maximum_delta,
        "backends": backends,
    }
    output = args.output or directory / f"renderer-consistency-{args.dpi}dpi.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))

    if maximum_delta > args.max_metric_delta:
        print(
            "renderer consistency gate failed: maximum backend-specific renderer "
            f"metric delta {maximum_delta:.9f} > {args.max_metric_delta:.9f}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
