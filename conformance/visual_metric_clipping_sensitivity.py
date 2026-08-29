#!/usr/bin/env python3
"""Calibrate the Khmer visual gate against deliberate top-edge ink clipping.

A whole-page RGB score can remain very high when a small but visually important
Khmer mark or glyph component is clipped. This probe removes the top rows of
each contiguous rendered-ink band from the actual reference raster and requires
the rendered-ink IoU gate to reject that corruption while the normal whole-page
similarity still passes.
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


def ink_row_runs(
    width: int,
    height: int,
    pixels: bytes,
    *,
    threshold: int,
) -> list[tuple[int, int]]:
    ink_rows: list[int] = []
    for y in range(height):
        row_offset = y * width * 3
        if any(
            pixel_is_ink(pixels, row_offset + x * 3, threshold)
            for x in range(width)
        ):
            ink_rows.append(y)

    if not ink_rows:
        return []

    runs: list[tuple[int, int]] = []
    start = previous = ink_rows[0]
    for row in ink_rows[1:]:
        if row != previous + 1:
            runs.append((start, previous))
            start = row
        previous = row
    runs.append((start, previous))
    return runs


def clip_top_ink_rows(
    source: Path,
    destination: Path,
    *,
    rows: int,
    threshold: int,
) -> dict[str, int]:
    width, height, pixels = read_ppm(source)
    clipped = bytearray(pixels)
    runs = ink_row_runs(width, height, pixels, threshold=threshold)
    removed_ink_pixels = 0

    for start, end in runs:
        for y in range(start, min(end + 1, start + rows)):
            for x in range(width):
                offset = (y * width + x) * 3
                if pixel_is_ink(pixels, offset, threshold):
                    clipped[offset : offset + 3] = b"\xff\xff\xff"
                    removed_ink_pixels += 1

    write_ppm(destination, width, height, bytes(clipped))
    return {
        "ink_band_count": len(runs),
        "removed_ink_pixels": removed_ink_pixels,
    }


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    return sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--clip-top-rows", type=int, default=15)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--whole-page-pass", type=float, default=0.995)
    parser.add_argument("--ink-reject", type=float, default=0.90)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.clip_top_rows <= 0:
        raise RuntimeError("--clip-top-rows must be positive")
    if not 0 <= args.ink_threshold <= 255:
        raise RuntimeError("--ink-threshold must be between 0 and 255")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, "system-harfbuzz")
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")

    clipped_dir = directory / f"sensitivity-clip-top-{args.clip_top_rows}"
    clipped_pages: list[Path] = []
    clipping_pages: list[dict[str, int]] = []
    for page_number, page in enumerate(reference_pages, start=1):
        destination = clipped_dir / page.name.replace(
            "system-harfbuzz",
            f"system-harfbuzz-clipped-top-{args.clip_top_rows}",
        )
        clipping = clip_top_ink_rows(
            page,
            destination,
            rows=args.clip_top_rows,
            threshold=args.ink_threshold,
        )
        clipping_pages.append({"page": page_number, **clipping})
        clipped_pages.append(destination)

    pages, summary = compare_page_sets(
        reference_pages,
        clipped_pages,
        ink_threshold=args.ink_threshold,
    )
    result = {
        "clip_top_rows": args.clip_top_rows,
        "whole_page_pass_threshold": args.whole_page_pass,
        "ink_reject_threshold": args.ink_reject,
        "clipping_pages": clipping_pages,
        "pages": pages,
        **summary,
    }
    output = directory / f"sensitivity-results-clip-top-{args.clip_top_rows}.json"
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    min_similarity = float(summary["minimum_page_similarity"])
    min_ink_iou = float(summary["minimum_page_ink_iou"])
    if min_similarity < args.whole_page_pass:
        raise RuntimeError(
            "clipping calibration is too destructive for the intended blank-page "
            f"dilution test: similarity={min_similarity:.6f}"
        )
    if min_ink_iou >= args.ink_reject:
        raise RuntimeError(
            "rendered-ink metric failed to detect deliberate top-edge clipping: "
            f"ink_iou={min_ink_iou:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
