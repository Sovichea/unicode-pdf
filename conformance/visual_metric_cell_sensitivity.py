#!/usr/bin/env python3
"""Calibrate the Khmer visual gate against a small 2D cell distortion.

The earlier localized probe shifts a full-height vertical slice. That is useful,
but a vertical-strip gate can still dilute a defect confined to one line or mark
band. This probe shifts a small rectangular portion of the rendered-ink bounding
box and requires the old full-height strip measurement to stay at or above the
normal threshold while the affected 2D cell falls below it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_metric_local_sensitivity import ink_bounds, numbered_pages, write_ppm


def measure_iou(
    reference: bytes,
    candidate: bytes,
    width: int,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    threshold: int,
) -> tuple[float, int, int]:
    intersection = 0
    union = 0
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            offset = (y * width + x) * 3
            ref_ink = pixel_is_ink(reference, offset, threshold)
            cand_ink = pixel_is_ink(candidate, offset, threshold)
            intersection += int(ref_ink and cand_ink)
            union += int(ref_ink or cand_ink)
    return (intersection / union if union else 1.0, intersection, union)


def distort_cell(
    source: Path,
    destination: Path,
    *,
    width_fraction: float,
    height_fraction: float,
    x_position: float,
    y_position: float,
    shift_x: int,
    threshold: int,
) -> dict[str, int | float]:
    width, height, pixels = read_ppm(source)
    left, top, right, bottom = ink_bounds(width, height, pixels, threshold)
    ink_width = right - left + 1
    ink_height = bottom - top + 1
    cell_width = max(1, round(ink_width * width_fraction))
    cell_height = max(1, round(ink_height * height_fraction))
    cell_left = left + round((ink_width - cell_width) * x_position)
    cell_top = top + round((ink_height - cell_height) * y_position)
    cell_right = min(right, cell_left + cell_width - 1)
    cell_bottom = min(bottom, cell_top + cell_height - 1)

    distorted = bytearray(pixels)
    for y in range(cell_top, cell_bottom + 1):
        for x in range(cell_left, cell_right + 1):
            offset = (y * width + x) * 3
            distorted[offset : offset + 3] = b"\xff\xff\xff"

    for y in range(cell_top, cell_bottom + 1):
        for x in range(cell_left, cell_right + 1):
            target_x = x + shift_x
            if target_x < 0 or target_x >= width:
                continue
            source_offset = (y * width + x) * 3
            target_offset = (y * width + target_x) * 3
            distorted[target_offset : target_offset + 3] = pixels[
                source_offset : source_offset + 3
            ]

    write_ppm(destination, width, height, bytes(distorted))
    strip_left = max(0, min(cell_left, cell_left + shift_x))
    strip_right = min(width - 1, max(cell_right, cell_right + shift_x))
    strip_iou, strip_intersection, strip_union = measure_iou(
        pixels,
        bytes(distorted),
        width,
        left=strip_left,
        top=top,
        right=strip_right,
        bottom=bottom,
        threshold=threshold,
    )
    cell_iou, cell_intersection, cell_union = measure_iou(
        pixels,
        bytes(distorted),
        width,
        left=strip_left,
        top=cell_top,
        right=strip_right,
        bottom=cell_bottom,
        threshold=threshold,
    )
    return {
        "ink_left": left,
        "ink_top": top,
        "ink_right": right,
        "ink_bottom": bottom,
        "cell_left": cell_left,
        "cell_top": cell_top,
        "cell_right": cell_right,
        "cell_bottom": cell_bottom,
        "width_fraction": width_fraction,
        "height_fraction": height_fraction,
        "x_position": x_position,
        "y_position": y_position,
        "shift_x": shift_x,
        "strip_ink_intersection_pixels": strip_intersection,
        "strip_ink_union_pixels": strip_union,
        "strip_ink_iou": strip_iou,
        "cell_ink_intersection_pixels": cell_intersection,
        "cell_ink_union_pixels": cell_union,
        "cell_ink_iou": cell_iou,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--width-fraction", type=float, default=0.10)
    parser.add_argument("--height-fraction", type=float, default=0.25)
    parser.add_argument("--x-position", type=float, default=0.0)
    parser.add_argument("--y-position", type=float, default=0.80)
    parser.add_argument("--shift-x", type=int, default=2)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--strip-pass", type=float, default=0.90)
    parser.add_argument("--cell-reject", type=float, default=0.90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    for name, value in (
        ("--width-fraction", args.width_fraction),
        ("--height-fraction", args.height_fraction),
    ):
        if not 0 < value <= 1:
            raise RuntimeError(f"{name} must be in (0, 1]")
    for name, value in (
        ("--x-position", args.x_position),
        ("--y-position", args.y_position),
    ):
        if not 0 <= value <= 1:
            raise RuntimeError(f"{name} must be in [0, 1]")
    if args.shift_x == 0:
        raise RuntimeError("--shift-x must be non-zero")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, "system-harfbuzz")
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    tag = (
        f"cell-w{args.width_fraction:.3f}-h{args.height_fraction:.3f}"
        f"-xp{args.x_position:.2f}-yp{args.y_position:.2f}-x{args.shift_x:+d}"
    ).replace(".", "p")
    distorted_dir = directory / f"sensitivity-{tag}"
    pages: list[dict[str, int | float]] = []
    for page_number, page in enumerate(reference_pages, start=1):
        destination = distorted_dir / page.name.replace(
            "system-harfbuzz", f"system-harfbuzz-{tag}"
        )
        metrics = distort_cell(
            page,
            destination,
            width_fraction=args.width_fraction,
            height_fraction=args.height_fraction,
            x_position=args.x_position,
            y_position=args.y_position,
            shift_x=args.shift_x,
            threshold=args.ink_threshold,
        )
        pages.append({"page": page_number, **metrics})

    minimum_strip_iou = min(float(page["strip_ink_iou"]) for page in pages)
    minimum_cell_iou = min(float(page["cell_ink_iou"]) for page in pages)
    result = {
        "candidate_prefix": f"sensitivity-{tag}/system-harfbuzz-{tag}",
        "strip_pass_threshold": args.strip_pass,
        "cell_reject_threshold": args.cell_reject,
        "minimum_strip_ink_iou": minimum_strip_iou,
        "minimum_cell_ink_iou": minimum_cell_iou,
        "pages": pages,
    }
    output = directory / f"sensitivity-results-{tag}.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if minimum_strip_iou < args.strip_pass:
        raise RuntimeError(
            "cell calibration is too destructive for vertical-strip dilution: "
            f"strip_ink_iou={minimum_strip_iou:.6f}"
        )
    if minimum_cell_iou >= args.cell_reject:
        raise RuntimeError(
            "cell-local measurement failed to detect deliberate distortion: "
            f"cell_ink_iou={minimum_cell_iou:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
