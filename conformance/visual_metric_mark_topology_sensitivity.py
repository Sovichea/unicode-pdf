#!/usr/bin/env python3
"""Inject a missing detached Khmer mark for topology-gate calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_metric_scale_sensitivity import write_ppm
from visual_vertical_zone_window_gate import zone_bounds


def components(pixels: bytes, width: int, top: int, bottom: int, threshold: int) -> list[list[tuple[int, int]]]:
    points = {(x, y) for y in range(top, bottom + 1) for x in range(width)
              if pixel_is_ink(pixels, (y * width + x) * 3, threshold)}
    found: list[list[tuple[int, int]]] = []
    while points:
        seed = points.pop()
        stack = [seed]
        comp = [seed]
        while stack:
            x, y = stack.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in points:
                        points.remove(neighbor)
                        stack.append(neighbor)
                        comp.append(neighbor)
        found.append(comp)
    return found


def erase_one_mark(source: Path, destination: Path, *, zone: str, zone_fraction: float,
                   threshold: int, min_component_pixels: int, max_component_pixels: int,
                   max_blank_row_gap: int) -> dict[str, object]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(width, height, pixels, pixels, ink_threshold=threshold,
                            max_blank_row_gap=max_blank_row_gap)
    choices: list[tuple[int, int, list[tuple[int, int]], int, int]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)
        for comp in components(pixels, width, zone_top, zone_bottom, threshold):
            if min_component_pixels <= len(comp) <= max_component_pixels:
                choices.append((len(comp), line_index, comp, zone_top, zone_bottom))
    if not choices:
        raise RuntimeError("no detached mark component met the configured size range")
    _, line_index, chosen, zone_top, zone_bottom = min(choices, key=lambda item: item[0])
    output = bytearray(pixels)
    for x, y in chosen:
        offset = (y * width + x) * 3
        output[offset:offset + 3] = b"\xff\xff\xff"
    write_ppm(destination, width, height, bytes(output))
    xs = [x for x, _ in chosen]
    ys = [y for _, y in chosen]
    return {"line": line_index, "zone_top": zone_top, "zone_bottom": zone_bottom,
            "erased_pixels": len(chosen), "bbox": [min(xs), min(ys), max(xs), max(ys)]}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--zone", choices=("upper", "lower"), default="upper")
    p.add_argument("--zone-fraction", type=float, default=0.25)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--min-component-pixels", type=int, default=4)
    p.add_argument("--max-component-pixels", type=int, default=80)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    return p.parse_args()


def main() -> int:
    a = parse_args()
    directory = Path(a.dir)
    pages = numbered_pages(directory, "system-harfbuzz")
    if not pages:
        raise RuntimeError(f"no system-harfbuzz rasters found in {directory}")
    label = f"{a.zone}-z{a.zone_fraction:.3f}".replace(".", "p")
    output_dir = directory / f"sensitivity-mark-topology-{label}"
    prefix = f"system-harfbuzz-mark-topology-{label}"
    results = []
    for page_number, page in enumerate(pages, start=1):
        destination = output_dir / f"{prefix}-{page_number}.ppm"
        results.append({"page": page_number, **erase_one_mark(
            page, destination, zone=a.zone, zone_fraction=a.zone_fraction,
            threshold=a.ink_threshold, min_component_pixels=a.min_component_pixels,
            max_component_pixels=a.max_component_pixels, max_blank_row_gap=a.max_blank_row_gap)})
    result = {"zone": a.zone, "zone_fraction": a.zone_fraction,
              "candidate_prefix": f"sensitivity-mark-topology-{label}/{prefix}", "pages": results}
    output = directory / f"sensitivity-results-mark-topology-{label}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
