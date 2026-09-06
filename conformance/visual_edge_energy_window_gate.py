#!/usr/bin/env python3
"""Gate localized Khmer edge sharpness in overlapping line windows.

Ink mass, topology, and counters can remain stable while a glyph becomes visibly
blurred or over-smoothed. This gate measures grayscale edge energy from adjacent
pixel differences so local loss or gain of stroke-edge sharpness is detected.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def luminance(pixels: bytes, offset: int) -> float:
    return (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) / 3.0


def edge_energy(
    pixels: bytes,
    width: int,
    *,
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> float:
    energy = 0.0
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            offset = (y * width + x) * 3
            center = luminance(pixels, offset)
            if x < right:
                energy += abs(center - luminance(pixels, offset + 3))
            if y < bottom:
                energy += abs(center - luminance(pixels, offset + width * 3))
    return energy


def similarity(reference: float, candidate: float) -> float:
    maximum = max(reference, candidate)
    if maximum <= 0.0:
        return 1.0
    return min(reference, candidate) / maximum


def compare_page(
    reference_path: Path,
    candidate_path: Path,
    *,
    window_fraction: float,
    stride_fraction: float,
    ink_threshold: int,
    max_blank_row_gap: int,
) -> dict[str, object]:
    width, height, reference = read_ppm(reference_path)
    cw, ch, candidate = read_ppm(candidate_path)
    if (width, height) != (cw, ch):
        raise RuntimeError(
            f"render dimensions differ: reference={width}x{height}, candidate={cw}x{ch}"
        )

    bands = detect_ink_bands(
        width,
        height,
        reference,
        candidate,
        ink_threshold=ink_threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    windows: list[dict[str, object]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs = [
            x
            for y in range(top, bottom + 1)
            for x in range(width)
            if pixel_is_ink(reference, (y * width + x) * 3, ink_threshold)
            or pixel_is_ink(candidate, (y * width + x) * 3, ink_threshold)
        ]
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

        for window_index, wl in enumerate(starts, start=1):
            wr = min(right, wl + window_width - 1)
            reference_energy = edge_energy(
                reference, width, left=wl, right=wr, top=top, bottom=bottom
            )
            candidate_energy = edge_energy(
                candidate, width, left=wl, right=wr, top=top, bottom=bottom
            )
            windows.append(
                {
                    "line": line_index,
                    "window": window_index,
                    "left": wl,
                    "right": wr,
                    "reference_edge_energy": reference_energy,
                    "candidate_edge_energy": candidate_energy,
                    "edge_energy_similarity": similarity(reference_energy, candidate_energy),
                }
            )

    if not windows:
        raise RuntimeError("no nonblank line windows were measured")
    return {
        "line_count": len(bands),
        "window_count": len(windows),
        "minimum_edge_energy_similarity": min(
            float(window["edge_energy_similarity"]) for window in windows
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
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float)
    parser.add_argument("--output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.0 < args.stride_fraction <= args.window_fraction <= 1.0:
        raise RuntimeError("invalid window configuration")

    directory = Path(args.dir)
    references = numbered_pages(directory, args.reference_prefix)
    candidates = numbered_pages(directory, args.candidate_prefix)
    if not references or len(references) != len(candidates):
        raise RuntimeError(
            f"rendered page counts differ: reference={len(references)}, candidate={len(candidates)}"
        )

    pages = [
        {
            "page": page_number,
            **compare_page(
                reference,
                candidate,
                window_fraction=args.window_fraction,
                stride_fraction=args.stride_fraction,
                ink_threshold=args.ink_threshold,
                max_blank_row_gap=args.max_blank_row_gap,
            ),
        }
        for page_number, (reference, candidate) in enumerate(
            zip(references, candidates), start=1
        )
    ]
    minimum = min(float(page["minimum_edge_energy_similarity"]) for page in pages)
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "window_fraction": args.window_fraction,
        "stride_fraction": args.stride_fraction,
        "minimum_edge_energy_similarity": minimum,
        "pages": pages,
    }
    output = Path(args.output) if args.output else directory / "edge-energy-results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum >= args.expect_below:
            print(
                f"edge-energy calibration failed: minimum={minimum:.10f}, expected < {args.expect_below}",
                file=sys.stderr,
            )
            return 1
        return 0
    if minimum < args.min_similarity:
        print(
            f"minimum localized edge-energy similarity {minimum:.10f} is below required {args.min_similarity:.10f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
