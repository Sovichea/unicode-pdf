#!/usr/bin/env python3
"""Gate localized Khmer stroke-edge orientation in overlapping line windows.

Strong edge pixels can remain present while a stroke contour changes direction. This
metric classifies local grayscale gradients into horizontal, vertical, and diagonal
orientation bins and compares the orientation field directly inside small line
windows. The project-wide 0.90 target therefore applies to local contour direction,
not only aggregate edge strength or pixel darkness.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def gray(pixel: bytes | bytearray, offset: int) -> int:
    return (int(pixel[offset]) + int(pixel[offset + 1]) + int(pixel[offset + 2])) // 3


def orientation_field(values: list[int], width: int, height: int, threshold: int) -> list[int]:
    """Return 0 for non-edge and 1..4 for horizontal/vertical/two diagonals."""
    field = [0] * (width * height)
    if width < 3 or height < 3:
        return field
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            i = y * width + x
            gx = values[i + 1] - values[i - 1]
            gy = values[i + width] - values[i - width]
            ax, ay = abs(gx), abs(gy)
            if max(ax, ay) < threshold:
                continue
            if ax >= 2 * ay:
                field[i] = 1  # vertical contour: intensity changes horizontally
            elif ay >= 2 * ax:
                field[i] = 2  # horizontal contour: intensity changes vertically
            elif gx * gy >= 0:
                field[i] = 3
            else:
                field[i] = 4
    return field


def compare_windows(reference_path: Path, candidate_path: Path, *, window_fraction: float,
                    stride_fraction: float, ink_threshold: int, edge_threshold: int,
                    min_edge_pixels: int, max_blank_row_gap: int) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    candidate_width, candidate_height, candidate = read_ppm(candidate_path)
    if (width, height) != (candidate_width, candidate_height):
        raise RuntimeError(
            "render dimensions differ: "
            f"reference={width}x{height}, candidate={candidate_width}x{candidate_height}"
        )

    bands = detect_ink_bands(width, height, reference, candidate,
                            ink_threshold=ink_threshold,
                            max_blank_row_gap=max_blank_row_gap)
    windows: list[dict[str, int | float]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs: list[int] = []
        for y in range(top, bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if min(reference[offset:offset + 3]) < ink_threshold or min(candidate[offset:offset + 3]) < ink_threshold:
                    xs.append(x)
        if not xs:
            continue
        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        window_width = max(3, round(ink_width * window_fraction))
        stride = max(1, round(ink_width * stride_fraction))
        starts = list(range(left, max(left + 1, right - window_width + 2), stride))
        final_start = max(left, right - window_width + 1)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)

        line_height = bottom - top + 1
        for window_index, window_left in enumerate(starts, start=1):
            window_right = min(right, window_left + window_width - 1)
            local_width = window_right - window_left + 1
            ref_gray: list[int] = []
            cand_gray: list[int] = []
            for y in range(top, bottom + 1):
                for x in range(window_left, window_right + 1):
                    offset = (y * width + x) * 3
                    ref_gray.append(gray(reference, offset))
                    cand_gray.append(gray(candidate, offset))
            ref_orientation = orientation_field(ref_gray, local_width, line_height, edge_threshold)
            cand_orientation = orientation_field(cand_gray, local_width, line_height, edge_threshold)
            edge_indices = [i for i, pair in enumerate(zip(ref_orientation, cand_orientation)) if pair[0] or pair[1]]
            if len(edge_indices) < min_edge_pixels:
                continue
            matches = sum(ref_orientation[i] == cand_orientation[i] and ref_orientation[i] != 0 for i in edge_indices)
            similarity = matches / len(edge_indices)
            windows.append({
                "line": line_index,
                "window": window_index,
                "left": window_left,
                "top": top,
                "right": window_right,
                "bottom": bottom,
                "edge_pixels": len(edge_indices),
                "matching_orientation_pixels": matches,
                "edge_orientation_similarity": similarity,
            })

    if not windows:
        raise RuntimeError("no window met the minimum edge-orientation threshold")
    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_edge_orientation_similarity": min(float(w["edge_orientation_similarity"]) for w in windows),
        "windows": windows,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--reference-prefix", default="system-harfbuzz")
    p.add_argument("--candidate-prefix", default="harfrust")
    p.add_argument("--window-fraction", type=float, default=0.025)
    p.add_argument("--stride-fraction", type=float, default=0.0125)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--edge-threshold", type=int, default=16)
    p.add_argument("--min-edge-pixels", type=int, default=8)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    p.add_argument("--min-similarity", type=float, default=0.90)
    p.add_argument("--expect-below", type=float, default=None)
    p.add_argument("--output", default=None)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.window_fraction <= 1.0:
        raise RuntimeError("--window-fraction must be in (0, 1]")
    if not 0.0 < args.stride_fraction <= args.window_fraction:
        raise RuntimeError("--stride-fraction must be in (0, window-fraction]")
    if not 0 <= args.edge_threshold <= 255:
        raise RuntimeError("--edge-threshold must be in [0, 255]")
    if args.min_edge_pixels <= 0:
        raise RuntimeError("--min-edge-pixels must be positive")

    directory = Path(args.dir)
    reference_pages = numbered_pages(directory, args.reference_prefix)
    candidate_pages = numbered_pages(directory, args.candidate_prefix)
    if not reference_pages:
        raise RuntimeError(f"no reference rasters found in {directory}")
    if len(reference_pages) != len(candidate_pages):
        raise RuntimeError(
            "rendered page counts differ: "
            f"reference={len(reference_pages)}, candidate={len(candidate_pages)}"
        )

    pages: list[dict[str, object]] = []
    for page_number, (reference, candidate) in enumerate(zip(reference_pages, candidate_pages), start=1):
        pages.append({
            "page": page_number,
            **compare_windows(reference, candidate,
                              window_fraction=args.window_fraction,
                              stride_fraction=args.stride_fraction,
                              ink_threshold=args.ink_threshold,
                              edge_threshold=args.edge_threshold,
                              min_edge_pixels=args.min_edge_pixels,
                              max_blank_row_gap=args.max_blank_row_gap),
        })

    minimum_similarity = min(float(page["minimum_edge_orientation_similarity"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "edge_threshold": args.edge_threshold,
        "min_edge_pixels": args.min_edge_pixels,
        "minimum_edge_orientation_similarity": minimum_similarity,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "edge-orientation-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_similarity >= args.expect_below:
            print(
                "edge-orientation calibration failed to detect deliberate distortion: "
                f"minimum_edge_orientation_similarity={minimum_similarity:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0
    if minimum_similarity < args.min_similarity:
        print(
            f"minimum edge-orientation similarity {minimum_similarity:.6f} is below required {args.min_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
