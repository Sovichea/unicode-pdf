#!/usr/bin/env python3
"""Gate localized Khmer counter/hole topology inside text-line windows.

Connected-component, centroid, spread, and ink-area metrics can miss a glyph whose
interior counter fills while the outer silhouette stays largely unchanged. This gate
counts enclosed white components inside overlapping text-line windows and rejects
backend-specific counter-count changes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def enclosed_white_count(pixels: bytes, width: int, top: int, bottom: int,
                         left: int, right: int, threshold: int,
                         min_hole_pixels: int, max_hole_pixels: int) -> int:
    white = {(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)
             if not pixel_is_ink(pixels, (y * width + x) * 3, threshold)}
    count = 0
    while white:
        seed = white.pop()
        stack = [seed]
        size = 1
        touches_border = seed[0] in (left, right) or seed[1] in (top, bottom)
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbor = (x + dx, y + dy)
                if neighbor in white:
                    white.remove(neighbor)
                    stack.append(neighbor)
                    size += 1
                    if neighbor[0] in (left, right) or neighbor[1] in (top, bottom):
                        touches_border = True
        if not touches_border and min_hole_pixels <= size <= max_hole_pixels:
            count += 1
    return count


def compare_page(reference_path: Path, candidate_path: Path, *, window_fraction: float,
                 stride_fraction: float, ink_threshold: int, min_hole_pixels: int,
                 max_hole_pixels: int, max_blank_row_gap: int) -> dict[str, object]:
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
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            rc = enclosed_white_count(reference, width, top, bottom, window_left, window_right,
                                      ink_threshold, min_hole_pixels, max_hole_pixels)
            cc = enclosed_white_count(candidate, width, top, bottom, window_left, window_right,
                                      ink_threshold, min_hole_pixels, max_hole_pixels)
            windows.append({"line": line_index, "window": window_index,
                            "left": window_left, "right": window_right, "top": top, "bottom": bottom,
                            "reference_counters": rc, "candidate_counters": cc,
                            "counter_count_delta": cc - rc,
                            "absolute_counter_count_delta": abs(cc - rc)})
    if not windows:
        raise RuntimeError("no text-line windows were measured")
    return {"line_count": len(bands), "window_count": len(windows),
            "maximum_counter_count_delta": max(int(w["absolute_counter_count_delta"]) for w in windows),
            "windows": windows}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--reference-prefix", default="system-harfbuzz")
    p.add_argument("--candidate-prefix", default="harfrust")
    p.add_argument("--window-fraction", type=float, default=0.10)
    p.add_argument("--stride-fraction", type=float, default=0.05)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--min-hole-pixels", type=int, default=2)
    p.add_argument("--max-hole-pixels", type=int, default=96)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    p.add_argument("--max-counter-delta", type=int, default=0)
    p.add_argument("--expect-above", type=float, default=None)
    p.add_argument("--output", default=None)
    return p.parse_args()


def main() -> int:
    a = parse_args()
    if not 0.0 < a.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < a.stride_fraction <= a.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if a.min_hole_pixels <= 0 or a.max_hole_pixels < a.min_hole_pixels:
        raise RuntimeError("hole size bounds are invalid")
    directory = Path(a.dir)
    refs = numbered_pages(directory, a.reference_prefix)
    candidates = numbered_pages(directory, a.candidate_prefix)
    if not refs or len(refs) != len(candidates):
        raise RuntimeError(f"rendered page counts differ: reference={len(refs)}, candidate={len(candidates)}")
    pages = [{"page": n, **compare_page(r, c, window_fraction=a.window_fraction,
              stride_fraction=a.stride_fraction, ink_threshold=a.ink_threshold,
              min_hole_pixels=a.min_hole_pixels, max_hole_pixels=a.max_hole_pixels,
              max_blank_row_gap=a.max_blank_row_gap)}
             for n, (r, c) in enumerate(zip(refs, candidates), start=1)]
    maximum = max(int(page["maximum_counter_count_delta"]) for page in pages)
    result = {"reference_prefix": a.reference_prefix, "candidate_prefix": a.candidate_prefix,
              "window_fraction": a.window_fraction, "stride_fraction": a.stride_fraction,
              "ink_threshold": a.ink_threshold, "min_hole_pixels": a.min_hole_pixels,
              "max_hole_pixels": a.max_hole_pixels, "maximum_counter_count_delta": maximum,
              "pages": pages}
    output = Path(a.output) if a.output else directory / "counter-window-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if a.expect_above is not None:
        if maximum <= a.expect_above:
            print(f"counter calibration failed: maximum={maximum}, expected > {a.expect_above}", file=sys.stderr)
            return 1
        return 0
    if maximum > a.max_counter_delta:
        print(f"maximum localized counter delta {maximum} exceeds allowed {a.max_counter_delta}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
