#!/usr/bin/env python3
"""Inject a centroid-preserving Khmer mark-zone shape deformation.

The probe horizontally scales upper- or lower-zone ink around that zone's own centroid while
leaving the rest of the raster untouched. It calibrates shape-sensitive metrics against a
defect that should move the centroid very little compared with a simple translation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_metric_scale_sensitivity import write_ppm
from visual_vertical_zone_window_gate import zone_bounds


def scale_label(scale: float) -> str:
    return f"{scale:.3f}".replace(".", "p")


def zone_centroid_x(
    pixels: bytes,
    width: int,
    top: int,
    bottom: int,
    threshold: int,
) -> tuple[float, int] | None:
    total_x = 0.0
    count = 0
    for y in range(top, bottom + 1):
        for x in range(width):
            offset = (y * width + x) * 3
            if pixel_is_ink(pixels, offset, threshold):
                total_x += x
                count += 1
    if count == 0:
        return None
    return total_x / count, count


def deform_page(
    source: Path,
    destination: Path,
    *,
    zone: str,
    zone_fraction: float,
    scale_x: float,
    threshold: int,
    max_blank_row_gap: int,
) -> dict[str, object]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(
        width,
        height,
        pixels,
        pixels,
        ink_threshold=threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    output = bytearray(pixels)
    changed_lines: list[dict[str, object]] = []

    for line_index, (top, bottom) in enumerate(bands, start=1):
        zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)
        centroid = zone_centroid_x(pixels, width, zone_top, zone_bottom, threshold)
        if centroid is None:
            continue
        cx, ink_count = centroid

        source_ink: list[tuple[int, int, bytes]] = []
        for y in range(zone_top, zone_bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if pixel_is_ink(pixels, offset, threshold):
                    source_ink.append((x, y, pixels[offset : offset + 3]))
                    output[offset : offset + 3] = b"\xff\xff\xff"

        placed = 0
        for x, y, rgb in source_ink:
            target_x = round(cx + (x - cx) * scale_x)
            if 0 <= target_x < width:
                offset = (y * width + target_x) * 3
                existing = output[offset : offset + 3]
                output[offset : offset + 3] = bytes(
                    min(existing[channel], rgb[channel]) for channel in range(3)
                )
                placed += 1

        changed_lines.append(
            {
                "line": line_index,
                "top": top,
                "bottom": bottom,
                "zone_top": zone_top,
                "zone_bottom": zone_bottom,
                "source_centroid_x": cx,
                "source_zone_ink_pixels": ink_count,
                "placed_zone_ink_samples": placed,
            }
        )

    write_ppm(destination, width, height, bytes(output))
    return {"line_count": len(bands), "changed_lines": changed_lines}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--zone", choices=("upper", "lower"), default="upper")
    parser.add_argument("--zone-fraction", type=float, default=0.25)
    parser.add_argument("--scale-x", type=float, default=1.20)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.zone_fraction <= 0.5:
        raise RuntimeError("--zone-fraction must be in (0, 0.5]")
    if args.scale_x <= 0.0 or args.scale_x == 1.0:
        raise RuntimeError("--scale-x must be positive and deliberately differ from 1.0")

    directory = Path(args.dir)
    pages = numbered_pages(directory, "system-harfbuzz")
    if not pages:
        raise RuntimeError(f"no system-harfbuzz rasters found in {directory}")

    zone_label = "" if args.zone == "upper" else "lower-"
    label = f"{zone_label}z{args.zone_fraction:.3f}-sx{scale_label(args.scale_x)}".replace(".", "p")
    output_dir = directory / f"sensitivity-mark-spread-{label}"
    prefix = f"system-harfbuzz-mark-spread-{label}"
    page_results: list[dict[str, object]] = []
    for page_number, page in enumerate(pages, start=1):
        destination = output_dir / f"{prefix}-{page_number}.ppm"
        page_results.append(
            {
                "page": page_number,
                **deform_page(
                    page,
                    destination,
                    zone=args.zone,
                    zone_fraction=args.zone_fraction,
                    scale_x=args.scale_x,
                    threshold=args.ink_threshold,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    result = {
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "scale_x": args.scale_x,
        "candidate_prefix": f"sensitivity-mark-spread-{label}/{prefix}",
        "pages": page_results,
    }
    output = directory / f"sensitivity-results-mark-spread-{label}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
