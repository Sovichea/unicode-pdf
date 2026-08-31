#!/usr/bin/env python3
"""Cross-check Khmer visual parity with an independent PDF rasterizer.

The primary visual pipeline uses Poppler's pdftoppm. This gate rerasterizes the
same HarfRust and system-HarfBuzz PDFs with MuPDF and applies the same pixel,
ink-IoU, and ink-only RGB metrics. This prevents a Poppler-specific rendering
behavior from giving false confidence about visual fidelity.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import compare_page_sets, run


def render_pages(pdf: Path, output_prefix: Path, dpi: int) -> list[Path]:
    pattern = output_prefix.parent / f"{output_prefix.name}-%d.ppm"
    run(
        [
            "mutool",
            "draw",
            "-q",
            "-F",
            "ppm",
            "-r",
            str(dpi),
            "-o",
            str(pattern),
            str(pdf),
        ]
    )
    pages = sorted(
        output_prefix.parent.glob(f"{output_prefix.name}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )
    if not pages:
        raise RuntimeError(f"mutool did not create pages for {pdf}")
    return pages


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument("--min-ink-iou", type=float, default=0.90)
    parser.add_argument("--min-ink-similarity", type=float, default=0.90)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    directory = args.dir
    reference_pdf = directory / f"{args.reference_prefix}.pdf"
    candidate_pdf = directory / f"{args.candidate_prefix}.pdf"
    for pdf in (reference_pdf, candidate_pdf):
        if not pdf.is_file():
            raise RuntimeError(f"missing PDF: {pdf}")

    renderer_dir = directory / f"mutool-{args.dpi}dpi"
    renderer_dir.mkdir(parents=True, exist_ok=True)
    reference_pages = render_pages(
        reference_pdf, renderer_dir / args.reference_prefix, args.dpi
    )
    candidate_pages = render_pages(
        candidate_pdf, renderer_dir / args.candidate_prefix, args.dpi
    )
    pages, summary = compare_page_sets(
        reference_pages,
        candidate_pages,
        ink_threshold=args.ink_threshold,
    )

    result = {
        "renderer": "MuPDF mutool draw",
        "dpi": args.dpi,
        "ink_threshold": args.ink_threshold,
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "thresholds": {
            "minimum_page_similarity": args.min_similarity,
            "minimum_page_ink_iou": args.min_ink_iou,
            "minimum_page_ink_similarity": args.min_ink_similarity,
        },
        "summary": summary,
        "pages": pages,
    }
    output = args.output or directory / f"cross-renderer-{args.dpi}dpi.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))

    failures: list[str] = []
    if float(summary["minimum_page_similarity"]) < args.min_similarity:
        failures.append(
            "minimum page similarity "
            f"{summary['minimum_page_similarity']:.9f} < {args.min_similarity:.9f}"
        )
    if float(summary["minimum_page_ink_iou"]) < args.min_ink_iou:
        failures.append(
            "minimum page ink IoU "
            f"{summary['minimum_page_ink_iou']:.9f} < {args.min_ink_iou:.9f}"
        )
    if float(summary["minimum_page_ink_similarity"]) < args.min_ink_similarity:
        failures.append(
            "minimum page ink similarity "
            f"{summary['minimum_page_ink_similarity']:.9f} < {args.min_ink_similarity:.9f}"
        )
    if failures:
        for failure in failures:
            print(f"visual cross-renderer gate failed: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
