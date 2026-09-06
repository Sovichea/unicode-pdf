#!/usr/bin/env python3
"""Calibrate the Khmer visual gate against a localized rendered distortion.

A page-level rendered-ink IoU can still dilute a defect confined to one small
Khmer region. This probe shifts only the central fraction of the actual rendered
ink bounding box and demonstrates that whole-page similarity and page ink IoU
can both remain above their normal gates while the affected region is visibly
wrong. It records a local ink IoU for that corrupted region so future visual
metrics can be calibrated against this failure mode.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import compare_page_sets, pixel_is_ink, read_ppm


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
        handle.write(pixels)


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    return sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )


def ink_bounds(
    width: int, height: int, pixels: bytes, threshold: int
) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            if pixel_is_ink(pixels, offset, threshold):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise RuntimeError("reference raster contains no rendered ink")
    return min(xs), min(ys), max(xs), max(ys)


def distort_central_ink_slice(
    source: Path,
    destination: Path,
    *,
    fraction: float,
    shift_x: int,
    threshold: int,
) -> dict[str, int | float]:
    width, height, pixels = read_ppm(source)
    left, top, right, bottom = ink_bounds(width, height, pixels, threshold)
    ink_width = right - left + 1
    slice_width = max(1, round(ink_width * fraction))
    slice_left = left + (ink_width - slice_width) // 2
    slice_right = slice_left + slice_width - 1

    distorted = bytearray(pixels)
    for y in range(top, bottom + 1):
        for x in range(slice_left, slice_right + 1):
            offset = (y * width + x) * 3
            distorted[offset : offset + 3] = b"\xff\xff\xff"

    for y in range(top, bottom + 1):
        for x in range(slice_left, slice_right + 1):
            target_x = x + shift_x
            if target_x < 0 or target_x >= width:
                continue
            source_offset = (y * width + x) * 3
            target_offset = (y * width + target_x) * 3
            distorted[target_offset : target_offset + 3] = pixels[
                source_offset : source_offset + 3
            ]

    write_ppm(destination, width, height, bytes(distorted))

    region_left = min(slice_left, slice_left + shift_x)
    region_right = max(slice_right, slice_right + shift_x)
    intersection = 0
    union = 0
    for y in range(top, bottom + 1):
        for x in range(max(0, region_left), min(width - 1, region_right) + 1):
            offset = (y * width + x) * 3
            ref_ink = pixel_is_ink(pixels, offset, threshold)
            cand_ink = pixel_is_ink(distorted, offset, threshold)
            intersection += int(ref_ink and cand_ink)
            union += int(ref_ink or cand_ink)

    local_ink_iou = intersection / union if union else 1.0
    return {
        "ink_left": left,
        "ink_top": top,
        "ink_right": right,
        "ink_bottom": bottom,
        "slice_left": slice_left,
        "slice_right": slice_right,
        "slice_fraction": fraction,
        "shift_x": shift_x,
        "local_ink_intersection_pixels": intersection,
        "local_ink_union_pixels": union,
        "local_ink_iou": local_ink_iou,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--fraction", type=float, default=0.10)
    parser.add_argument("--shift-x", type=int, default=2)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--whole-page-pass", type=float, default=0.995)
    parser.add_argument("--page-ink-pass", type=float, default=0.90)
    parser.add_argument("--local-ink-reject", type=float, default=0.90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0 < args.fraction <= 1:
        raise RuntimeError("--fraction must be in (0, 1]")
    if args.shift_x == 0:
        raise RuntimeError("--shift-x must be non-zero")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, "system-harfbuzz")
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    tag = f"local-{args.fraction:.3f}-x{args.shift_x:+d}".replace(".", "p")
    distorted_dir = directory / f"sensitivity-{tag}"
    distorted_pages: list[Path] = []
    local_pages: list[dict[str, int | float]] = []
    for page_number, page in enumerate(reference_pages, start=1):
        destination = distorted_dir / page.name.replace(
            "system-harfbuzz", f"system-harfbuzz-{tag}"
        )
        local = distort_central_ink_slice(
            page,
            destination,
            fraction=args.fraction,
            shift_x=args.shift_x,
            threshold=args.ink_threshold,
        )
        local_pages.append({"page": page_number, **local})
        distorted_pages.append(destination)

    pages, summary = compare_page_sets(
        reference_pages, distorted_pages, ink_threshold=args.ink_threshold
    )
    minimum_local_ink_iou = min(
        float(page["local_ink_iou"]) for page in local_pages
    )
    result = {
        "slice_fraction": args.fraction,
        "shift_x": args.shift_x,
        "whole_page_pass_threshold": args.whole_page_pass,
        "page_ink_pass_threshold": args.page_ink_pass,
        "local_ink_reject_threshold": args.local_ink_reject,
        "local_pages": local_pages,
        "minimum_local_ink_iou": minimum_local_ink_iou,
        "pages": pages,
        **summary,
    }
    output = directory / f"sensitivity-results-{tag}.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    min_similarity = float(summary["minimum_page_similarity"])
    min_page_ink_iou = float(summary["minimum_page_ink_iou"])
    if min_similarity < args.whole_page_pass:
        raise RuntimeError(
            "localized calibration is too destructive for whole-page dilution: "
            f"similarity={min_similarity:.6f}"
        )
    if min_page_ink_iou < args.page_ink_pass:
        raise RuntimeError(
            "localized calibration is too destructive for page-level ink dilution: "
            f"page_ink_iou={min_page_ink_iou:.6f}"
        )
    if minimum_local_ink_iou >= args.local_ink_reject:
        raise RuntimeError(
            "local rendered-ink measurement failed to detect the deliberate "
            f"distortion: local_ink_iou={minimum_local_ink_iou:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
