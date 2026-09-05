#!/usr/bin/env python3
"""Gate localized Khmer edge-pixel fidelity in overlapping line windows.

Aggregate edge energy can stay similar when boundary pixels move or redistribute.
This gate instead identifies high-contrast edge pixels in either raster and compares
their grayscale values directly, keeping the visual 0.90 target local to glyph edges.
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


def edge_mask(values: list[int], width: int, height: int, threshold: int) -> list[bool]:
    mask = [False] * (width * height)
    for y in range(height):
        row = y * width
        for x in range(width):
            i = row + x
            if x + 1 < width and abs(values[i] - values[i + 1]) >= threshold:
                mask[i] = True
                mask[i + 1] = True
            if y + 1 < height and abs(values[i] - values[i + width]) >= threshold:
                mask[i] = True
                mask[i + width] = True
    return mask


def compare_windows(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    edge_threshold: int,
    min_edge_pixels: int,
    max_blank_row_gap: int,
) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    candidate_width, candidate_height, candidate = read_ppm(candidate_path)
    if (width, height) != (candidate_width, candidate_height):
        raise RuntimeError(
            "render dimensions differ: "
            f"reference={width}x{height}, candidate={candidate_width}x{candidate_height}"
        )

    bands = detect_ink_bands(
        width,
        height,
        reference,
        candidate,
        ink_threshold=ink_threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    windows: list[dict[str, int | float]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs: list[int] = []
        for y in range(top, bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if min(reference[offset : offset + 3]) < ink_threshold or min(
                    candidate[offset : offset + 3]
                ) < ink_threshold:
                    xs.append(x)
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

            ref_edges = edge_mask(ref_gray, local_width, line_height, edge_threshold)
            cand_edges = edge_mask(cand_gray, local_width, line_height, edge_threshold)
            edge_indices = [
                index
                for index, (ref_edge, cand_edge) in enumerate(zip(ref_edges, cand_edges))
                if ref_edge or cand_edge
            ]
            if len(edge_indices) < min_edge_pixels:
                continue

            absolute_error = sum(
                abs(ref_gray[index] - cand_gray[index]) for index in edge_indices
            )
            similarity = 1.0 - absolute_error / (len(edge_indices) * 255)
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": window_left,
                    "top": top,
                    "right": window_right,
                    "bottom": bottom,
                    "edge_pixels": len(edge_indices),
                    "edge_pixel_similarity": similarity,
                }
            )

    if not windows:
        raise RuntimeError("no window met the minimum edge-pixel threshold")

    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_edge_pixel_similarity": min(
            float(window["edge_pixel_similarity"]) for window in windows
        ),
        "windows": windows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--window-fraction", type=float, default=0.05)
    parser.add_argument("--stride-fraction", type=float, default=0.025)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--edge-threshold", type=int, default=16)
    parser.add_argument("--min-edge-pixels", type=int, default=16)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


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
    for page_number, (reference, candidate) in enumerate(
        zip(reference_pages, candidate_pages), start=1
    ):
        pages.append(
            {
                "page": page_number,
                **compare_windows(
                    reference,
                    candidate,
                    window_fraction=args.window_fraction,
                    stride_fraction=args.stride_fraction,
                    ink_threshold=args.ink_threshold,
                    edge_threshold=args.edge_threshold,
                    min_edge_pixels=args.min_edge_pixels,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    minimum_similarity = min(
        float(page["minimum_edge_pixel_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "edge_threshold": args.edge_threshold,
        "min_edge_pixels": args.min_edge_pixels,
        "minimum_edge_pixel_similarity": minimum_similarity,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "edge-pixel-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_similarity >= args.expect_below:
            print(
                "edge-pixel calibration failed to detect deliberate distortion: "
                f"minimum_edge_pixel_similarity={minimum_similarity:.6f}, expected < "
                f"{args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_similarity < args.min_similarity:
        print(
            f"minimum edge-pixel similarity {minimum_similarity:.6f} is below required "
            f"{args.min_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
