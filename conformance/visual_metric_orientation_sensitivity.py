#!/usr/bin/env python3
"""Inject several local Khmer contour moves while preserving ink area/connectivity.

This calibration is intentionally stronger than the single-pixel boundary probe used by
other gates. Each move removes one ink pixel and adds one adjacent ink pixel while
keeping the chosen component connected. Touched pixels are not reused, so the moves
cannot immediately undo one another.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_metric_mark_area_sensitivity import component_count
from visual_metric_mark_topology_sensitivity import components
from visual_metric_scale_sensitivity import write_ppm


def deform_component(source: Path, destination: Path, *, threshold: int,
                     min_component_pixels: int, max_component_pixels: int,
                     max_blank_row_gap: int, mutation_count: int) -> dict[str, object]:
    width, height, original = read_ppm(source)
    pixels = bytearray(original)
    forbidden: set[tuple[int, int]] = set()
    mutations: list[dict[str, object]] = []

    for _ in range(mutation_count):
        bands = detect_ink_bands(width, height, pixels, pixels,
                                ink_threshold=threshold,
                                max_blank_row_gap=max_blank_row_gap)
        choices: list[tuple[int, int, list[tuple[int, int]]]] = []
        for line_index, (top, bottom) in enumerate(bands, start=1):
            for component in components(pixels, width, top, bottom, threshold):
                if min_component_pixels <= len(component) <= max_component_pixels:
                    choices.append((len(component), line_index, component))

        applied = False
        for _, line_index, chosen in sorted(choices, reverse=True):
            points = set(chosen)
            for x, y in chosen:
                if (x, y) in forbidden:
                    continue
                ink_neighbors = sum((x + dx, y + dy) in points
                                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
                if ink_neighbors < 2:
                    continue
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if ((nx, ny) in forbidden or not (0 <= nx < width and 0 <= ny < height)
                            or (nx, ny) in points):
                        continue
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

                    old = (y * width + x) * 3
                    new = (ny * width + nx) * 3
                    pixels[old:old + 3] = b"\xff\xff\xff"
                    pixels[new:new + 3] = b"\x00\x00\x00"
                    forbidden.update({(x, y), (nx, ny)})
                    mutations.append({
                        "line": line_index,
                        "component_pixels": len(points),
                        "removed": [x, y],
                        "added": [nx, ny],
                    })
                    applied = True
                    break
                if applied:
                    break
            if applied:
                break
        if not applied:
            raise RuntimeError(
                f"only applied {len(mutations)} of {mutation_count} requested contour mutations"
            )

    write_ppm(destination, width, height, bytes(pixels))
    return {
        "mutation_count": len(mutations),
        "mutations": mutations,
        "ink_area_delta": 0,
        "all_mutations_preserve_component_connectivity": True,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--min-component-pixels", type=int, default=30)
    p.add_argument("--max-component-pixels", type=int, default=600)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    p.add_argument("--mutation-count", type=int, default=8)
    a = p.parse_args()
    if a.mutation_count <= 0:
        raise RuntimeError("--mutation-count must be positive")

    directory = Path(a.dir)
    pages = numbered_pages(directory, "system-harfbuzz")
    if not pages:
        raise RuntimeError(f"no system-harfbuzz rasters found in {directory}")
    output_dir = directory / "sensitivity-orientation-area-preserving"
    prefix = "system-harfbuzz-orientation-area-preserving"
    results = []
    for page_number, page in enumerate(pages, 1):
        destination = output_dir / f"{prefix}-{page_number}.ppm"
        results.append({"page": page_number, **deform_component(
            page, destination,
            threshold=a.ink_threshold,
            min_component_pixels=a.min_component_pixels,
            max_component_pixels=a.max_component_pixels,
            max_blank_row_gap=a.max_blank_row_gap,
            mutation_count=a.mutation_count,
        )})

    result = {
        "candidate_prefix": f"sensitivity-orientation-area-preserving/{prefix}",
        "requested_mutation_count": a.mutation_count,
        "pages": results,
    }
    out = directory / "sensitivity-results-orientation-area-preserving.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
