#!/usr/bin/env python3
"""Gate Khmer raster parity on localized rendered-ink overlap.

Whole-page similarity and page-level ink IoU can hide a defect confined to a
small part of a line. This experimental gate divides the combined rendered-ink
bounding box into a 2D grid and requires every sufficiently populated cell to
preserve the configured ink IoU. ``--rows 1`` retains the earlier vertical-strip
behavior for compatibility and calibration comparisons.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    return sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )


def compare_regions(
    reference: Path,
    candidate: Path,
    *,
    column_count: int,
    row_count: int,
    ink_threshold: int,
    min_region_ink_pixels: int,
) -> dict[str, object]:
    width, height, ref = read_ppm(reference)
    cand_width, cand_height, cand = read_ppm(candidate)
    if (width, height) != (cand_width, cand_height):
        raise RuntimeError(
            "render dimensions differ: "
            f"reference={width}x{height}, candidate={cand_width}x{cand_height}"
        )

    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            if pixel_is_ink(ref, offset, ink_threshold) or pixel_is_ink(
                cand, offset, ink_threshold
            ):
                xs.append(x)
                ys.append(y)

    if not xs:
        return {
            "ink_left": None,
            "ink_top": None,
            "ink_right": None,
            "ink_bottom": None,
            "regions": [],
            "minimum_region_ink_iou": 1.0,
        }

    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    ink_width = right - left + 1
    ink_height = bottom - top + 1
    regions: list[dict[str, int | float]] = []

    for row_index in range(row_count):
        region_top = top + (ink_height * row_index) // row_count
        region_bottom = top + (ink_height * (row_index + 1)) // row_count - 1
        if region_bottom < region_top:
            continue
        for column_index in range(column_count):
            region_left = left + (ink_width * column_index) // column_count
            region_right = left + (ink_width * (column_index + 1)) // column_count - 1
            if region_right < region_left:
                continue

            intersection = 0
            union = 0
            reference_ink = 0
            candidate_ink = 0
            for y in range(region_top, region_bottom + 1):
                for x in range(region_left, region_right + 1):
                    offset = (y * width + x) * 3
                    ref_ink = pixel_is_ink(ref, offset, ink_threshold)
                    cand_ink = pixel_is_ink(cand, offset, ink_threshold)
                    reference_ink += int(ref_ink)
                    candidate_ink += int(cand_ink)
                    intersection += int(ref_ink and cand_ink)
                    union += int(ref_ink or cand_ink)

            if union < min_region_ink_pixels:
                continue
            regions.append(
                {
                    "region": len(regions) + 1,
                    "row": row_index + 1,
                    "column": column_index + 1,
                    "left": region_left,
                    "top": region_top,
                    "right": region_right,
                    "bottom": region_bottom,
                    "reference_ink_pixels": reference_ink,
                    "candidate_ink_pixels": candidate_ink,
                    "ink_intersection_pixels": intersection,
                    "ink_union_pixels": union,
                    "ink_iou": intersection / union,
                }
            )

    if not regions:
        raise RuntimeError(
            "no rendered-ink region met the minimum population threshold"
        )

    return {
        "ink_left": left,
        "ink_top": top,
        "ink_right": right,
        "ink_bottom": bottom,
        "regions": regions,
        "minimum_region_ink_iou": min(float(region["ink_iou"]) for region in regions),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument(
        "--regions",
        type=int,
        default=10,
        help="Number of columns in the rendered-ink grid.",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=1,
        help="Number of rows in the rendered-ink grid; 1 preserves vertical-strip behavior.",
    )
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-region-ink-pixels", type=int, default=64)
    parser.add_argument("--min-region-ink-iou", type=float, default=0.90)
    parser.add_argument(
        "--expect-below",
        type=float,
        default=None,
        help="Calibration mode: pass only when the minimum region IoU is below this value.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.regions <= 0:
        raise RuntimeError("--regions must be positive")
    if args.rows <= 0:
        raise RuntimeError("--rows must be positive")
    if args.min_region_ink_pixels <= 0:
        raise RuntimeError("--min-region-ink-pixels must be positive")

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
                **compare_regions(
                    reference,
                    candidate,
                    column_count=args.regions,
                    row_count=args.rows,
                    ink_threshold=args.ink_threshold,
                    min_region_ink_pixels=args.min_region_ink_pixels,
                ),
            }
        )

    minimum = min(float(page["minimum_region_ink_iou"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "region_count": args.regions,
        "row_count": args.rows,
        "ink_threshold": args.ink_threshold,
        "min_region_ink_pixels": args.min_region_ink_pixels,
        "min_region_ink_iou": args.min_region_ink_iou,
        "expect_below": args.expect_below,
        "minimum_region_ink_iou": minimum,
        "pages": pages,
    }
    output_name = (
        "region-calibration.json" if args.expect_below is not None else "region-results.json"
    )
    (directory / output_name).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum >= args.expect_below:
            print(
                "region-aware calibration failed to detect deliberate distortion: "
                f"minimum_region_ink_iou={minimum:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum < args.min_region_ink_iou:
        print(
            "minimum localized rendered-ink IoU "
            f"{minimum:.6f} is below required {args.min_region_ink_iou:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
