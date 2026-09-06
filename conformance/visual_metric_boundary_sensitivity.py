#!/usr/bin/env python3
"""Inject a one-pixel Khmer outline deformation while preserving ink area/topology."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_metric_mark_area_sensitivity import component_count
from visual_metric_mark_topology_sensitivity import components
from visual_metric_scale_sensitivity import write_ppm


def deform_one_component(source: Path, destination: Path, *, threshold: int,
                         min_component_pixels: int, max_component_pixels: int,
                         max_blank_row_gap: int) -> dict[str, object]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(width, height, pixels, pixels,
                            ink_threshold=threshold, max_blank_row_gap=max_blank_row_gap)
    choices: list[tuple[int, int, list[tuple[int, int]]]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        for component in components(pixels, width, top, bottom, threshold):
            if min_component_pixels <= len(component) <= max_component_pixels:
                choices.append((len(component), line_index, component))
    if not choices:
        raise RuntimeError("no ink component met the configured size range")

    for _, line_index, chosen in sorted(choices, reverse=True):
        points = set(chosen)
        for x, y in chosen:
            ink_neighbors = sum((x + dx, y + dy) in points
                                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
            if ink_neighbors < 2:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height) or (nx, ny) in points:
                    continue
                # The replacement must stay attached to the original component after p is removed.
                remaining = points - {(x, y)}
                if component_count(remaining) != 1:
                    continue
                if not any((nx + ax, ny + ay) in remaining
                           for ax in (-1, 0, 1) for ay in (-1, 0, 1)
                           if ax != 0 or ay != 0):
                    continue
                mutated = remaining | {(nx, ny)}
                if len(mutated) != len(points) or component_count(mutated) != 1:
                    continue
                output = bytearray(pixels)
                old = (y * width + x) * 3
                new = (ny * width + nx) * 3
                output[old:old + 3] = b"\xff\xff\xff"
                output[new:new + 3] = b"\x00\x00\x00"
                write_ppm(destination, width, height, bytes(output))
                return {"line": line_index, "component_pixels": len(points),
                        "removed": [x, y], "added": [nx, ny],
                        "ink_area_delta": 0, "component_count_before": 1,
                        "component_count_after": 1}
    raise RuntimeError("no eligible one-pixel boundary mutation preserved connectivity")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--min-component-pixels", type=int, default=30)
    p.add_argument("--max-component-pixels", type=int, default=600)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    a = p.parse_args()
    directory = Path(a.dir)
    pages = numbered_pages(directory, "system-harfbuzz")
    if not pages:
        raise RuntimeError(f"no system-harfbuzz rasters found in {directory}")
    output_dir = directory / "sensitivity-boundary-area-preserving"
    prefix = "system-harfbuzz-boundary-area-preserving"
    results = []
    for page_number, page in enumerate(pages, 1):
        destination = output_dir / f"{prefix}-{page_number}.ppm"
        results.append({"page": page_number, **deform_one_component(
            page, destination, threshold=a.ink_threshold,
            min_component_pixels=a.min_component_pixels,
            max_component_pixels=a.max_component_pixels,
            max_blank_row_gap=a.max_blank_row_gap)})
    result = {"candidate_prefix": f"sensitivity-boundary-area-preserving/{prefix}",
              "pages": results}
    out = directory / "sensitivity-results-boundary-area-preserving.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
