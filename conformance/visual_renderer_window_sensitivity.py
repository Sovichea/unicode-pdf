#!/usr/bin/env python3
"""Inject a localized MuPDF-only tone defect for renderer-consistency calibration.

The production comparison checks whether Poppler-versus-MuPDF disagreement is the
same for HarfRust and system HarfBuzz. This helper copies the MuPDF reference
rasters unchanged, then changes only existing ink pixels in a narrow window of the
candidate MuPDF raster. Because the ink mask is preserved, window geometry remains
comparable while local renderer similarity becomes backend-specific.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from visual_line_gate import numbered_pages
from visual_metric_tone_sensitivity import distort_line_tone


def fraction_label(value: float) -> str:
    return f"{value:.3f}".replace(".", "p")


def position_label(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--width-fraction", type=float, default=0.025)
    parser.add_argument("--x-position", type=float, default=0.4875)
    parser.add_argument("--tone", type=int, default=160)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.width_fraction <= 1.0:
        raise RuntimeError("--width-fraction must be in (0, 1]")
    if not 0.0 <= args.x_position <= 1.0:
        raise RuntimeError("--x-position must be in [0, 1]")
    if not 0 <= args.tone < args.ink_threshold <= 255:
        raise RuntimeError("--tone must preserve the ink mask: 0 <= tone < ink-threshold <= 255")

    source_dir = args.dir / f"mutool-{args.dpi}dpi"
    if not source_dir.is_dir():
        raise RuntimeError(
            f"missing MuPDF raster directory {source_dir}; run visual_cross_renderer_gate.py first"
        )

    width_label = fraction_label(args.width_fraction)
    x_label = position_label(args.x_position)
    output_name = (
        f"mutool-{args.dpi}dpi-renderer-sensitivity-"
        f"w{width_label}-xp{x_label}-v{args.tone}"
    )
    output_dir = args.dir / output_name
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_pages = numbered_pages(source_dir, args.reference_prefix)
    candidate_pages = numbered_pages(source_dir, args.candidate_prefix)
    if len(reference_pages) != len(candidate_pages):
        raise RuntimeError(
            "backend MuPDF page counts differ: "
            f"reference={len(reference_pages)}, candidate={len(candidate_pages)}"
        )

    pages: list[dict[str, object]] = []
    for page_number, (reference, candidate) in enumerate(
        zip(reference_pages, candidate_pages), start=1
    ):
        reference_output = output_dir / reference.name
        candidate_output = output_dir / candidate.name
        shutil.copyfile(reference, reference_output)
        distortion = distort_line_tone(
            candidate,
            candidate_output,
            width_fraction=args.width_fraction,
            x_position=args.x_position,
            tone=args.tone,
            ink_threshold=args.ink_threshold,
            max_blank_row_gap=args.max_blank_row_gap,
        )
        pages.append(
            {
                "page": page_number,
                "reference_source": str(reference),
                "candidate_source": str(candidate),
                "reference_output": str(reference_output),
                "candidate_output": str(candidate_output),
                **distortion,
            }
        )

    result = {
        "source_mutool_dir": source_dir.name,
        "output_mutool_dir": output_name,
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "width_fraction": args.width_fraction,
        "x_position": args.x_position,
        "tone": args.tone,
        "ink_threshold": args.ink_threshold,
        "pages": pages,
    }
    output_path = args.dir / "renderer-window-sensitivity.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
