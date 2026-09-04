#!/usr/bin/env python3
"""Gate localized Khmer stroke placement using grayscale ink centroids."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def centroid(pixels: bytes, width: int, *, top: int, bottom: int, left: int, right: int) -> tuple[float, float, float]:
    mass = sx = sy = 0.0
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            offset = (y * width + x) * 3
            darkness = 255.0 - (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) / 3.0
            if darkness <= 0.0:
                continue
            mass += darkness
            sx += darkness * x
            sy += darkness * y
    if mass <= 0.0:
        raise RuntimeError("empty centroid window")
    return sx / mass, sy / mass, mass


def compare(reference_path: Path, candidate_path: Path, *, window_fraction: float, stride_fraction: float, ink_threshold: int, max_blank_row_gap: int) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    cw, ch, candidate = read_ppm(candidate_path)
    if (width, height) != (cw, ch):
        raise RuntimeError("render dimensions differ")
    bands = detect_ink_bands(width, height, reference, candidate, ink_threshold=ink_threshold, max_blank_row_gap=max_blank_row_gap)
    windows = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs = [x for y in range(top, bottom + 1) for x in range(width) if pixel_is_ink(reference, (y * width + x) * 3, ink_threshold) or pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)]
        if not xs:
            continue
        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        ww = max(3, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - ww + 2), stride))
        final = max(left, right - ww + 1)
        if not starts or starts[-1] != final:
            starts.append(final)
        for index, wl in enumerate(starts, start=1):
            wr = min(right, wl + ww - 1)
            try:
                rx, ry, rm = centroid(reference, width, top=top, bottom=bottom, left=wl, right=wr)
                cx, cy, cm = centroid(candidate, width, top=top, bottom=bottom, left=wl, right=wr)
            except RuntimeError:
                continue
            dx = abs(rx - cx) / max(1, ww)
            dy = abs(ry - cy) / max(1, bottom - top + 1)
            similarity = max(0.0, 1.0 - max(dx, dy))
            windows.append({"line": line_index, "window": index, "left": wl, "right": wr, "top": top, "bottom": bottom, "reference_mass": rm, "candidate_mass": cm, "normalized_dx": dx, "normalized_dy": dy, "centroid_similarity": similarity})
    if not windows:
        raise RuntimeError("no centroid windows found")
    return {"line_count": len(bands), "windows": windows, "minimum_centroid_similarity": min(float(w["centroid_similarity"]) for w in windows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    directory = Path(args.dir)
    refs = numbered_pages(directory, args.reference_prefix)
    cands = numbered_pages(directory, args.candidate_prefix)
    if not refs or len(refs) != len(cands):
        raise RuntimeError("reference/candidate page mismatch")
    pages = [{"page": n, **compare(r, c, window_fraction=args.window_fraction, stride_fraction=args.stride_fraction, ink_threshold=args.ink_threshold, max_blank_row_gap=args.max_blank_row_gap)} for n, (r, c) in enumerate(zip(refs, cands), start=1)]
    minimum = min(float(p["minimum_centroid_similarity"]) for p in pages)
    result = {"minimum_centroid_similarity": minimum, "window_fraction": args.window_fraction, "stride_fraction": args.stride_fraction, "pages": pages}
    output = Path(args.output) if args.output else directory / "stroke-centroid-results.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.expect_below is not None:
        if minimum >= args.expect_below:
            print(f"centroid calibration failed: {minimum:.6f} >= {args.expect_below:.6f}", file=sys.stderr)
            return 1
        return 0
    if minimum < args.min_similarity:
        print(f"minimum centroid similarity {minimum:.6f} is below required {args.min_similarity:.6f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
