#!/usr/bin/env python3
"""Calibrate the Khmer visual gate against subtle horizontal text scaling.

A repair can preserve the glyph outlines while getting advances or text-matrix
scaling slightly wrong. On a mostly white PDF page, a sub-percent horizontal
stretch can still score very highly in a whole-page RGB comparison. This probe
rescales the rendered ink bounding box around its left edge and requires the
rendered-ink IoU gate to reject that geometry error while the whole-page score
still passes.
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


def ink_bounds(
    width: int,
    height: int,
    pixels: bytes,
    *,
    threshold: int,
) -> tuple[int, int, int, int]:
    xs: list[int] = []
    ys: list[int] = []
    for y in range(height):
        row_offset = y * width * 3
        for x in range(width):
            offset = row_offset + x * 3
            if pixel_is_ink(pixels, offset, threshold):
                xs.append(x)
                ys.append(y)
    if not xs:
        raise RuntimeError("reference raster contains no rendered ink")
    return min(xs), min(ys), max(xs), max(ys)


def scale_ink_horizontally(
    source: Path,
    destination: Path,
    *,
    scale: float,
    threshold: int,
) -> dict[str, int | float]:
    width, height, pixels = read_ppm(source)
    x_min, y_min, x_max, y_max = ink_bounds(
        width, height, pixels, threshold=threshold
    )
    source_width = x_max - x_min + 1
    scaled_width = max(1, round(source_width * scale))
    scaled = bytearray(b"\xff" * len(pixels))

    for y in range(y_min, y_max + 1):
        for destination_x in range(scaled_width):
            source_x = min(
                source_width - 1,
                max(0, round(destination_x / scale)),
            )
            source_offset = (y * width + x_min + source_x) * 3
            output_x = x_min + destination_x
            if output_x >= width:
                break
            output_offset = (y * width + output_x) * 3
            scaled[output_offset : output_offset + 3] = pixels[
                source_offset : source_offset + 3
            ]

    write_ppm(destination, width, height, bytes(scaled))
    return {
        "x_min": x_min,
        "y_min": y_min,
        "x_max": x_max,
        "y_max": y_max,
        "source_ink_bbox_width": source_width,
        "scaled_ink_bbox_width": scaled_width,
        "scale": scale,
    }


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    return sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )


def scale_label(scale: float) -> str:
    return f"{scale:.4f}".replace(".", "p")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--scale", type=float, default=1.005)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--whole-page-pass", type=float, default=0.995)
    parser.add_argument("--ink-reject", type=float, default=0.90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.scale <= 0:
        raise RuntimeError("--scale must be positive")
    if args.scale == 1.0:
        raise RuntimeError("--scale must deliberately differ from 1.0")
    if not 0 <= args.ink_threshold <= 255:
        raise RuntimeError("--ink-threshold must be between 0 and 255")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, "system-harfbuzz")
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    label = scale_label(args.scale)
    scaled_dir = directory / f"sensitivity-scale-x-{label}"
    scaled_pages: list[Path] = []
    scaling_pages: list[dict[str, int | float]] = []
    for page_number, page in enumerate(reference_pages, start=1):
        destination = scaled_dir / page.name.replace(
            "system-harfbuzz",
            f"system-harfbuzz-scale-x-{label}",
        )
        scaling = scale_ink_horizontally(
            page,
            destination,
            scale=args.scale,
            threshold=args.ink_threshold,
        )
        scaling_pages.append({"page": page_number, **scaling})
        scaled_pages.append(destination)

    pages, summary = compare_page_sets(
        reference_pages,
        scaled_pages,
        ink_threshold=args.ink_threshold,
    )
    result = {
        "horizontal_scale": args.scale,
        "whole_page_pass_threshold": args.whole_page_pass,
        "ink_reject_threshold": args.ink_reject,
        "scaling_pages": scaling_pages,
        "pages": pages,
        **summary,
    }
    output = directory / f"sensitivity-results-scale-x-{label}.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    min_similarity = float(summary["minimum_page_similarity"])
    min_ink_iou = float(summary["minimum_page_ink_iou"])
    if min_similarity < args.whole_page_pass:
        raise RuntimeError(
            "scale calibration is too destructive for the intended blank-page "
            f"dilution test: similarity={min_similarity:.6f}"
        )
    if min_ink_iou >= args.ink_reject:
        raise RuntimeError(
            "rendered-ink metric failed to detect deliberate horizontal scaling: "
            f"ink_iou={min_ink_iou:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
