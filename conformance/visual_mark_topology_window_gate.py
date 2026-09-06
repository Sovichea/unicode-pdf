#!/usr/bin/env python3
"""Gate localized Khmer mark topology using connected-component counts.

Centroid and spread metrics can miss a detached vowel sign or diacritic that disappears
entirely. This gate counts 8-connected ink components inside overlapping upper/lower
mark-zone windows and rejects backend-specific component-count changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_vertical_zone_window_gate import zone_bounds


def component_count(pixels: bytes, width: int, top: int, bottom: int, left: int, right: int, threshold: int, min_pixels: int) -> int:
    points = {(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)
              if pixel_is_ink(pixels, (y * width + x) * 3, threshold)}
    count = 0
    while points:
        seed = points.pop()
        stack = [seed]
        size = 1
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
                        size += 1
        if size >= min_pixels:
            count += 1
    return count


def compare_page(reference_path: Path, candidate_path: Path, *, zone: str, zone_fraction: float,
                 window_fraction: float, stride_fraction: float, ink_threshold: int,
                 min_component_pixels: int, max_blank_row_gap: int) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    cw, ch, candidate = read_ppm(candidate_path)
    if (width, height) != (cw, ch):
        raise RuntimeError(f"render dimensions differ: reference={width}x{height}, candidate={cw}x{ch}")
    bands = detect_ink_bands(width, height, reference, candidate, ink_threshold=ink_threshold,
                            max_blank_row_gap=max_blank_row_gap)
    windows: list[dict[str, object]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs = [x for y in range(top, bottom + 1) for x in range(width)
              if pixel_is_ink(reference, (y * width + x) * 3, ink_threshold)
              or pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)]
        if not xs:
            continue
        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        window_width = max(1, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)
        zone_top, zone_bottom = zone_bounds(top, bottom, zone, zone_fraction)
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            rc = component_count(reference, width, zone_top, zone_bottom, window_left, window_right,
                                 ink_threshold, min_component_pixels)
            cc = component_count(candidate, width, zone_top, zone_bottom, window_left, window_right,
                                 ink_threshold, min_component_pixels)
            windows.append({"line": line_index, "window": window_index, "left": window_left,
                            "right": window_right, "zone_top": zone_top, "zone_bottom": zone_bottom,
                            "reference_components": rc, "candidate_components": cc,
                            "component_count_delta": cc - rc,
                            "absolute_component_count_delta": abs(cc - rc)})
    if not windows:
        raise RuntimeError("no mark-zone windows were measured")
    return {"line_count": len(bands), "window_count": len(windows),
            "maximum_component_count_delta": max(int(w["absolute_component_count_delta"]) for w in windows),
            "windows": windows}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--reference-prefix", default="system-harfbuzz")
    p.add_argument("--candidate-prefix", default="harfrust")
    p.add_argument("--zone", choices=("upper", "lower"), default="upper")
    p.add_argument("--zone-fraction", type=float, default=0.25)
    p.add_argument("--window-fraction", type=float, default=0.05)
    p.add_argument("--stride-fraction", type=float, default=0.025)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--min-component-pixels", type=int, default=4)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    p.add_argument("--max-component-delta", type=int, default=0)
    p.add_argument("--expect-above", type=float, default=None)
    p.add_argument("--output", default=None)
    return p.parse_args()


def main() -> int:
    a = parse_args()
    if not 0.0 < a.zone_fraction <= 0.5 or not 0.0 < a.window_fraction <= 1.0:
        raise RuntimeError("zone/window fractions are out of range")
    if not 0.0 < a.stride_fraction <= a.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if a.min_component_pixels <= 0 or a.max_component_delta < 0:
        raise RuntimeError("component thresholds must be non-negative and min pixels positive")
    directory = Path(a.dir)
    refs = numbered_pages(directory, a.reference_prefix)
    candidates = numbered_pages(directory, a.candidate_prefix)
    if not refs or len(refs) != len(candidates):
        raise RuntimeError(f"rendered page counts differ: reference={len(refs)}, candidate={len(candidates)}")
    pages = [{"page": n, **compare_page(r, c, zone=a.zone, zone_fraction=a.zone_fraction,
              window_fraction=a.window_fraction, stride_fraction=a.stride_fraction,
              ink_threshold=a.ink_threshold, min_component_pixels=a.min_component_pixels,
              max_blank_row_gap=a.max_blank_row_gap)}
             for n, (r, c) in enumerate(zip(refs, candidates), start=1)]
    maximum = max(int(page["maximum_component_count_delta"]) for page in pages)
    result = {"reference_prefix": a.reference_prefix, "candidate_prefix": a.candidate_prefix,
              "zone": a.zone, "zone_fraction": a.zone_fraction, "window_fraction": a.window_fraction,
              "stride_fraction": a.stride_fraction, "ink_threshold": a.ink_threshold,
              "min_component_pixels": a.min_component_pixels,
              "maximum_component_count_delta": maximum, "pages": pages}
    output = Path(a.output) if a.output else directory / "mark-topology-window-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if a.expect_above is not None:
        if maximum <= a.expect_above:
            print(f"mark topology calibration failed: maximum={maximum}, expected > {a.expect_above}", file=sys.stderr)
            return 1
        return 0
    if maximum > a.max_component_delta:
        print(f"maximum localized mark component delta {maximum} exceeds allowed {a.max_component_delta}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
