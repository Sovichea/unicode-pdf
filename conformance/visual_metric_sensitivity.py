#!/usr/bin/env python3
"""Calibrate the Khmer visual gate against a deliberate rendered displacement.

The parity gate's whole-page RGB score can be dominated by blank page area. This
probe shifts the actual reference Khmer raster by a small number of pixels and
requires that the rendered-ink IoU gate reject the displacement even when the
whole-page similarity would still pass its normal threshold.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import compare_page_sets, read_ppm


def write_ppm(path: Path, width: int, height: int, pixels: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
        handle.write(pixels)


def shift_raster(source: Path, destination: Path, dx: int, dy: int) -> None:
    width, height, pixels = read_ppm(source)
    shifted = bytearray(b"\xff" * len(pixels))
    for y in range(height):
        target_y = y + dy
        if target_y < 0 or target_y >= height:
            continue
        for x in range(width):
            target_x = x + dx
            if target_x < 0 or target_x >= width:
                continue
            source_offset = (y * width + x) * 3
            target_offset = (target_y * width + target_x) * 3
            shifted[target_offset : target_offset + 3] = pixels[
                source_offset : source_offset + 3
            ]
    write_ppm(destination, width, height, bytes(shifted))


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    return sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--shift-x", type=int, default=2)
    parser.add_argument("--shift-y", type=int, default=0)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--whole-page-pass", type=float, default=0.995)
    parser.add_argument("--ink-reject", type=float, default=0.90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, "system-harfbuzz")
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    shifted_dir = directory / "sensitivity-shift"
    shifted_pages: list[Path] = []
    for page in reference_pages:
        destination = shifted_dir / page.name.replace(
            "system-harfbuzz", "system-harfbuzz-shifted"
        )
        shift_raster(page, destination, args.shift_x, args.shift_y)
        shifted_pages.append(destination)

    pages, summary = compare_page_sets(
        reference_pages, shifted_pages, ink_threshold=args.ink_threshold
    )
    result = {
        "shift_x": args.shift_x,
        "shift_y": args.shift_y,
        "whole_page_pass_threshold": args.whole_page_pass,
        "ink_reject_threshold": args.ink_reject,
        "pages": pages,
        **summary,
    }
    output = directory / "sensitivity-results.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    min_similarity = float(summary["minimum_page_similarity"])
    min_ink_iou = float(summary["minimum_page_ink_iou"])
    if min_similarity < args.whole_page_pass:
        raise RuntimeError(
            "calibration shift is too destructive for the intended blank-page "
            f"dilution test: similarity={min_similarity:.6f}"
        )
    if min_ink_iou >= args.ink_reject:
        raise RuntimeError(
            "rendered-ink metric failed to detect deliberate displacement: "
            f"ink_iou={min_ink_iou:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
