#!/usr/bin/env python3
"""Gate localized Khmer boundary displacement with a symmetric edge-distance score.

Area, centroid, topology, and counter counts can all remain unchanged while a small
part of a Khmer glyph outline is bent or displaced. This gate extracts binary ink
edges inside overlapping line windows and measures the worst nearest-edge distance
in both directions. The reported similarity is 1 - distance / tolerance, clamped
to [0, 1], so the project-wide 0.90 visual target remains directly interpretable.
"""

from __future__ import annotations

import argparse
import json
from math import hypot
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def edge_points(pixels: bytes, width: int, height: int, top: int, bottom: int,
                left: int, right: int, threshold: int) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if not pixel_is_ink(pixels, (y * width + x) * 3, threshold):
                continue
            boundary = False
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx < 0 or nx >= width or ny < 0 or ny >= height:
                    boundary = True
                    break
                if not pixel_is_ink(pixels, (ny * width + nx) * 3, threshold):
                    boundary = True
                    break
            if boundary:
                points.append((x, y))
    return points


def directed_max_distance(source: list[tuple[int, int]], target: list[tuple[int, int]]) -> float:
    if not source:
        return 0.0
    if not target:
        return float("inf")
    return max(min(hypot(x - tx, y - ty) for tx, ty in target) for x, y in source)


def compare_page(reference_path: Path, candidate_path: Path, *, window_fraction: float,
                 stride_fraction: float, ink_threshold: int, max_blank_row_gap: int,
                 distance_tolerance: float) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    cw, ch, candidate = read_ppm(candidate_path)
    if (width, height) != (cw, ch):
        raise RuntimeError(f"render dimensions differ: reference={width}x{height}, candidate={cw}x{ch}")
    bands = detect_ink_bands(width, height, reference, candidate,
                            ink_threshold=ink_threshold, max_blank_row_gap=max_blank_row_gap)
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
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            ref_edges = edge_points(reference, width, height, top, bottom, window_left, window_right, ink_threshold)
            cand_edges = edge_points(candidate, width, height, top, bottom, window_left, window_right, ink_threshold)
            if not ref_edges and not cand_edges:
                continue
            distance = max(directed_max_distance(ref_edges, cand_edges),
                           directed_max_distance(cand_edges, ref_edges))
            similarity = max(0.0, 1.0 - distance / distance_tolerance)
            windows.append({"line": line_index, "window": window_index,
                            "left": window_left, "right": window_right,
                            "max_symmetric_edge_distance": distance,
                            "boundary_distance_similarity": similarity})
    if not windows:
        raise RuntimeError("no nonblank line windows were measured")
    return {"line_count": len(bands), "window_count": len(windows),
            "maximum_symmetric_edge_distance": max(float(w["max_symmetric_edge_distance"]) for w in windows),
            "minimum_boundary_distance_similarity": min(float(w["boundary_distance_similarity"]) for w in windows),
            "windows": windows}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--reference-prefix", default="system-harfbuzz")
    p.add_argument("--candidate-prefix", default="harfrust")
    p.add_argument("--window-fraction", type=float, default=0.05)
    p.add_argument("--stride-fraction", type=float, default=0.025)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    p.add_argument("--distance-tolerance", type=float, default=10.0)
    p.add_argument("--min-similarity", type=float, default=0.90)
    p.add_argument("--expect-below", type=float)
    p.add_argument("--output")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    if not 0 < a.stride_fraction <= a.window_fraction <= 1 or a.distance_tolerance <= 0:
        raise RuntimeError("invalid window or distance configuration")
    directory = Path(a.dir)
    refs, cands = numbered_pages(directory, a.reference_prefix), numbered_pages(directory, a.candidate_prefix)
    if not refs or len(refs) != len(cands):
        raise RuntimeError(f"rendered page counts differ: reference={len(refs)}, candidate={len(cands)}")
    pages = [{"page": i, **compare_page(r, c, window_fraction=a.window_fraction,
              stride_fraction=a.stride_fraction, ink_threshold=a.ink_threshold,
              max_blank_row_gap=a.max_blank_row_gap, distance_tolerance=a.distance_tolerance)}
             for i, (r, c) in enumerate(zip(refs, cands), 1)]
    minimum = min(float(p["minimum_boundary_distance_similarity"]) for p in pages)
    maximum = max(float(p["maximum_symmetric_edge_distance"]) for p in pages)
    result = {"reference_prefix": a.reference_prefix, "candidate_prefix": a.candidate_prefix,
              "window_fraction": a.window_fraction, "stride_fraction": a.stride_fraction,
              "distance_tolerance": a.distance_tolerance,
              "minimum_boundary_distance_similarity": minimum,
              "maximum_symmetric_edge_distance": maximum, "pages": pages}
    out = Path(a.output) if a.output else directory / "boundary-distance-window-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if a.expect_below is not None:
        if minimum >= a.expect_below:
            print(f"boundary-distance calibration failed: minimum={minimum:.10f}, expected < {a.expect_below}", file=sys.stderr)
            return 1
        return 0
    if minimum < a.min_similarity:
        print(f"minimum boundary-distance similarity {minimum:.10f} is below required {a.min_similarity:.10f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
