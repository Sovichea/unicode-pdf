#!/usr/bin/env python3
"""Gate localized Khmer stroke structure with per-scanline transition counts.

Area, connected components, counters and centroid can remain stable while a local
Khmer stroke opens, closes, merges or splits. For overlapping text-line windows,
this gate compares ink/white transition counts independently on every horizontal
row and vertical column. The worst normalized scanline score is used so a small
structural defect cannot be diluted by unaffected rows or columns.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def transitions(bits: list[bool]) -> int:
    return sum(a != b for a, b in zip(bits, bits[1:]))


def pair_similarity(reference_count: int, candidate_count: int) -> float:
    return max(0.0, 1.0 - abs(reference_count - candidate_count) /
               max(1, reference_count, candidate_count))


def compare_page(reference_path: Path, candidate_path: Path, *, window_fraction: float,
                 stride_fraction: float, ink_threshold: int,
                 max_blank_row_gap: int) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    cw, ch, candidate = read_ppm(candidate_path)
    if (width, height) != (cw, ch):
        raise RuntimeError(f"render dimensions differ: reference={width}x{height}, candidate={cw}x{ch}")

    bands = detect_ink_bands(width, height, reference, candidate,
                            ink_threshold=ink_threshold, max_blank_row_gap=max_blank_row_gap)
    windows: list[dict[str, object]] = []
    for line_index, (top, bottom) in enumerate(bands, 1):
        xs = [x for y in range(top, bottom + 1) for x in range(width)
              if pixel_is_ink(reference, (y * width + x) * 3, ink_threshold)
              or pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)]
        if not xs:
            continue
        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        window_width = max(2, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)

        for window_index, wl in enumerate(starts, 1):
            wr = min(right, wl + window_width - 1)
            scores: list[float] = []
            for y in range(top, bottom + 1):
                ref_count = transitions([pixel_is_ink(reference, (y * width + x) * 3, ink_threshold)
                                         for x in range(wl, wr + 1)])
                cand_count = transitions([pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)
                                          for x in range(wl, wr + 1)])
                scores.append(pair_similarity(ref_count, cand_count))
            for x in range(wl, wr + 1):
                ref_count = transitions([pixel_is_ink(reference, (y * width + x) * 3, ink_threshold)
                                         for y in range(top, bottom + 1)])
                cand_count = transitions([pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)
                                          for y in range(top, bottom + 1)])
                scores.append(pair_similarity(ref_count, cand_count))
            windows.append({"line": line_index, "window": window_index,
                            "left": wl, "right": wr,
                            "minimum_scanline_transition_similarity": min(scores)})

    if not windows:
        raise RuntimeError("no nonblank line windows were measured")
    return {"line_count": len(bands), "window_count": len(windows),
            "minimum_scanline_transition_similarity": min(float(w["minimum_scanline_transition_similarity"]) for w in windows),
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
    p.add_argument("--min-similarity", type=float, default=0.90)
    p.add_argument("--expect-below", type=float)
    p.add_argument("--output")
    return p.parse_args()


def main() -> int:
    a = parse_args()
    if not 0 < a.stride_fraction <= a.window_fraction <= 1:
        raise RuntimeError("invalid window configuration")
    directory = Path(a.dir)
    refs, cands = numbered_pages(directory, a.reference_prefix), numbered_pages(directory, a.candidate_prefix)
    if not refs or len(refs) != len(cands):
        raise RuntimeError(f"rendered page counts differ: reference={len(refs)}, candidate={len(cands)}")
    pages = [{"page": i, **compare_page(r, c, window_fraction=a.window_fraction,
              stride_fraction=a.stride_fraction, ink_threshold=a.ink_threshold,
              max_blank_row_gap=a.max_blank_row_gap)}
             for i, (r, c) in enumerate(zip(refs, cands), 1)]
    minimum = min(float(p["minimum_scanline_transition_similarity"]) for p in pages)
    result = {"reference_prefix": a.reference_prefix, "candidate_prefix": a.candidate_prefix,
              "window_fraction": a.window_fraction, "stride_fraction": a.stride_fraction,
              "minimum_scanline_transition_similarity": minimum, "pages": pages}
    out = Path(a.output) if a.output else directory / "scanline-transition-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if a.expect_below is not None:
        if minimum >= a.expect_below:
            print(f"scanline calibration failed: minimum={minimum:.10f}, expected < {a.expect_below}", file=sys.stderr)
            return 1
        return 0
    if minimum < a.min_similarity:
        print(f"minimum scanline transition similarity {minimum:.10f} is below required {a.min_similarity:.10f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
