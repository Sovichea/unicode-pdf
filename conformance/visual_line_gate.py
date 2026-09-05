#!/usr/bin/env python3
"""Gate Khmer raster parity on text-line-local rendered-ink fidelity.

The page-grid gate catches localized defects, but its row boundaries are tied to
the page ink box rather than to actual text lines. This companion gate detects
horizontal ink bands first, then subdivides each band into equal-width segments.
Each populated segment is checked both for binary rendered-ink overlap and for
RGB similarity over the union of rendered ink. The second metric catches visible
stroke-weight or tone regressions that leave the occupied-pixel mask unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm


def numbered_pages(directory: Path, prefix: str) -> list[Path]:
    return sorted(
        directory.glob(f"{prefix}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )


def detect_ink_bands(
    width: int,
    height: int,
    reference: bytes,
    candidate: bytes,
    *,
    ink_threshold: int,
    max_blank_row_gap: int,
) -> list[tuple[int, int]]:
    occupied_rows: list[bool] = []
    for y in range(height):
        occupied = False
        for x in range(width):
            offset = (y * width + x) * 3
            if pixel_is_ink(reference, offset, ink_threshold) or pixel_is_ink(
                candidate, offset, ink_threshold
            ):
                occupied = True
                break
        occupied_rows.append(occupied)

    bands: list[tuple[int, int]] = []
    start: int | None = None
    last_ink: int | None = None
    for y, occupied in enumerate(occupied_rows):
        if occupied:
            if start is None:
                start = y
            last_ink = y
            continue
        if start is not None and last_ink is not None and y - last_ink > max_blank_row_gap:
            bands.append((start, last_ink))
            start = None
            last_ink = None
    if start is not None and last_ink is not None:
        bands.append((start, last_ink))
    return bands


def compare_line_segments(
    reference_path: Path,
    candidate_path: Path,
    *,
    segment_count: int,
    ink_threshold: int,
    min_segment_ink_pixels: int,
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
    if not bands:
        return {
            "line_count": 0,
            "segments": [],
            "minimum_line_segment_ink_iou": 1.0,
            "minimum_line_segment_ink_similarity": 1.0,
        }

    segments: list[dict[str, int | float]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs: list[int] = []
        for y in range(top, bottom + 1):
            for x in range(width):
                offset = (y * width + x) * 3
                if pixel_is_ink(reference, offset, ink_threshold) or pixel_is_ink(
                    candidate, offset, ink_threshold
                ):
                    xs.append(x)
        if not xs:
            continue

        left, right = min(xs), max(xs)
        ink_width = right - left + 1
        for segment_index in range(segment_count):
            segment_left = left + (ink_width * segment_index) // segment_count
            segment_right = left + (ink_width * (segment_index + 1)) // segment_count - 1
            if segment_right < segment_left:
                continue

            intersection = 0
            union = 0
            reference_ink = 0
            candidate_ink = 0
            ink_absolute_error = 0
            for y in range(top, bottom + 1):
                for x in range(segment_left, segment_right + 1):
                    offset = (y * width + x) * 3
                    ref_ink = pixel_is_ink(reference, offset, ink_threshold)
                    cand_ink = pixel_is_ink(candidate, offset, ink_threshold)
                    reference_ink += int(ref_ink)
                    candidate_ink += int(cand_ink)
                    intersection += int(ref_ink and cand_ink)
                    union += int(ref_ink or cand_ink)
                    if ref_ink or cand_ink:
                        ink_absolute_error += sum(
                            abs(reference[offset + channel] - candidate[offset + channel])
                            for channel in range(3)
                        )

            if union < min_segment_ink_pixels:
                continue
            ink_iou = intersection / union
            ink_similarity = 1.0 - ink_absolute_error / (union * 3 * 255)
            segments.append(
                {
                    "line": line_index,
                    "segment": segment_index + 1,
                    "left": segment_left,
                    "top": top,
                    "right": segment_right,
                    "bottom": bottom,
                    "reference_ink_pixels": reference_ink,
                    "candidate_ink_pixels": candidate_ink,
                    "ink_intersection_pixels": intersection,
                    "ink_union_pixels": union,
                    "ink_iou": ink_iou,
                    "ink_similarity": ink_similarity,
                }
            )

    if not segments:
        raise RuntimeError("no line segment met the minimum rendered-ink population threshold")

    return {
        "line_count": len(bands),
        "segments": segments,
        "minimum_line_segment_ink_iou": min(float(segment["ink_iou"]) for segment in segments),
        "minimum_line_segment_ink_similarity": min(
            float(segment["ink_similarity"]) for segment in segments
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--segments", type=int, default=10)
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--min-segment-ink-pixels", type=int, default=64)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-line-segment-ink-iou", type=float, default=0.90)
    parser.add_argument("--min-line-segment-ink-similarity", type=float, default=0.90)
    parser.add_argument(
        "--expect-below",
        type=float,
        default=None,
        help="Calibration mode: pass only when a line segment falls below this IoU.",
    )
    parser.add_argument(
        "--expect-similarity-below",
        type=float,
        default=None,
        help=(
            "Tone calibration mode: require binary IoU to stay above the normal gate "
            "while at least one line segment falls below this ink similarity."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.segments <= 0:
        raise RuntimeError("--segments must be positive")
    if args.min_segment_ink_pixels <= 0:
        raise RuntimeError("--min-segment-ink-pixels must be positive")
    if args.max_blank_row_gap < 0:
        raise RuntimeError("--max-blank-row-gap must be non-negative")
    if args.expect_below is not None and args.expect_similarity_below is not None:
        raise RuntimeError("choose only one calibration expectation")

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
                **compare_line_segments(
                    reference,
                    candidate,
                    segment_count=args.segments,
                    ink_threshold=args.ink_threshold,
                    min_segment_ink_pixels=args.min_segment_ink_pixels,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    minimum_iou = min(float(page["minimum_line_segment_ink_iou"]) for page in pages)
    minimum_similarity = min(
        float(page["minimum_line_segment_ink_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "segment_count": args.segments,
        "ink_threshold": args.ink_threshold,
        "min_segment_ink_pixels": args.min_segment_ink_pixels,
        "max_blank_row_gap": args.max_blank_row_gap,
        "min_line_segment_ink_iou": args.min_line_segment_ink_iou,
        "min_line_segment_ink_similarity": args.min_line_segment_ink_similarity,
        "expect_below": args.expect_below,
        "expect_similarity_below": args.expect_similarity_below,
        "minimum_line_segment_ink_iou": minimum_iou,
        "minimum_line_segment_ink_similarity": minimum_similarity,
        "pages": pages,
    }
    if args.expect_similarity_below is not None:
        output_name = "line-tone-calibration.json"
    elif args.expect_below is not None:
        output_name = "line-calibration.json"
    else:
        output_name = "line-results.json"
    (directory / output_name).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if args.expect_below is not None:
        if minimum_iou >= args.expect_below:
            print(
                "line-aware calibration failed to detect deliberate distortion: "
                f"minimum_line_segment_ink_iou={minimum_iou:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.expect_similarity_below is not None:
        if minimum_iou < args.min_line_segment_ink_iou:
            print(
                "tone calibration changed the binary ink mask too much: "
                f"minimum_line_segment_ink_iou={minimum_iou:.6f}, required >= "
                f"{args.min_line_segment_ink_iou:.6f}",
                file=sys.stderr,
            )
            return 1
        if minimum_similarity >= args.expect_similarity_below:
            print(
                "line-local tone calibration failed to detect deliberate distortion: "
                f"minimum_line_segment_ink_similarity={minimum_similarity:.6f}, expected < "
                f"{args.expect_similarity_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_iou < args.min_line_segment_ink_iou:
        print(
            "minimum line-local rendered-ink IoU "
            f"{minimum_iou:.6f} is below required {args.min_line_segment_ink_iou:.6f}",
            file=sys.stderr,
        )
        return 1
    if minimum_similarity < args.min_line_segment_ink_similarity:
        print(
            "minimum line-local rendered-ink similarity "
            f"{minimum_similarity:.6f} is below required "
            f"{args.min_line_segment_ink_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
