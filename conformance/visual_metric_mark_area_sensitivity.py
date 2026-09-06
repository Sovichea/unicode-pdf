#!/usr/bin/env python3
"""Inject partial erosion of a detached Khmer mark for ink-area calibration."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_metric_scale_sensitivity import write_ppm
from visual_metric_mark_topology_sensitivity import components
from visual_vertical_zone_window_gate import zone_bounds


def component_count(points: set[tuple[int, int]]) -> int:
    remaining = set(points)
    count = 0
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        count += 1
        while stack:
            x, y = stack.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
    return count


def erode_one_mark(source: Path, destination: Path, *, zone: str, zone_fraction: float,
                   threshold: int, min_component_pixels: int, max_component_pixels: int,
                   shave_fraction: float, max_blank_row_gap: int) -> dict[str, object]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(
        width,
        height,
        pixels,
        pixels,
        ink_threshold=threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    choices: list[tuple[int, int, list[tuple[int, int]], int, int]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)
        for component in components(pixels, width, zone_top, zone_bottom, threshold):
            if min_component_pixels <= len(component) <= max_component_pixels:
                choices.append((len(component), line_index, component, zone_top, zone_bottom))
    if not choices:
        raise RuntimeError("no detached mark component met the configured size range")

    for _, line_index, chosen, zone_top, zone_bottom in sorted(choices, reverse=True):
        xs = [x for x, _ in chosen]
        min_x, max_x = min(xs), max(xs)
        width_pixels = max_x - min_x + 1
        shave_columns = max(1, math.ceil(width_pixels * shave_fraction))
        cutoff = max_x - shave_columns + 1
        erased = [point for point in chosen if point[0] >= cutoff]
        remaining = set(chosen) - set(erased)
        if not erased or len(remaining) < min_component_pixels:
            continue
        if component_count(remaining) != 1:
            continue

        output = bytearray(pixels)
        for x, y in erased:
            offset = (y * width + x) * 3
            output[offset:offset + 3] = b"\xff\xff\xff"
        write_ppm(destination, width, height, bytes(output))
        ys = [y for _, y in chosen]
        return {
            "line": line_index,
            "zone_top": zone_top,
            "zone_bottom": zone_bottom,
            "original_pixels": len(chosen),
            "erased_pixels": len(erased),
            "remaining_pixels": len(remaining),
            "shave_fraction": shave_fraction,
            "bbox": [min_x, min(ys), max_x, max(ys)],
            "remaining_component_count": 1,
        }

    raise RuntimeError("no eligible mark remained connected after partial erosion")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--zone", choices=("upper", "lower"), default="upper")
    parser.add_argument("--zone-fraction", type=float, default=0.25)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-component-pixels", type=int, default=20)
    parser.add_argument("--max-component-pixels", type=int, default=80)
    parser.add_argument("--shave-fraction", type=float, default=0.30)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.shave_fraction < 1.0:
        raise RuntimeError("--shave-fraction must be in (0, 1)")
    directory = Path(args.dir)
    pages = numbered_pages(directory, "system-harfbuzz")
    if not pages:
        raise RuntimeError(f"no system-harfbuzz rasters found in {directory}")

    label = f"{args.zone}-z{args.zone_fraction:.3f}-s{args.shave_fraction:.3f}".replace(".", "p")
    output_dir = directory / f"sensitivity-mark-area-{label}"
    prefix = f"system-harfbuzz-mark-area-{label}"
    results = []
    for page_number, page in enumerate(pages, start=1):
        destination = output_dir / f"{prefix}-{page_number}.ppm"
        results.append(
            {
                "page": page_number,
                **erode_one_mark(
                    page,
                    destination,
                    zone=args.zone,
                    zone_fraction=args.zone_fraction,
                    threshold=args.ink_threshold,
                    min_component_pixels=args.min_component_pixels,
                    max_component_pixels=args.max_component_pixels,
                    shave_fraction=args.shave_fraction,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    result = {
        "zone": args.zone,
        "zone_fraction": args.zone_fraction,
        "shave_fraction": args.shave_fraction,
        "candidate_prefix": f"sensitivity-mark-area-{label}/{prefix}",
        "pages": results,
    }
    output = directory / f"sensitivity-results-mark-area-{label}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
