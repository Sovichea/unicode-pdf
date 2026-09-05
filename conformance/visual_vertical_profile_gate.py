#!/usr/bin/env python3
"""Gate Khmer vertical mark placement using per-line rendered-ink profiles.

Khmer vowel signs and diacritics often occupy narrow vertical zones above or below
the main consonant body. Area overlap can dilute a vertical-placement defect, so
this gate compares the normalized rendered-ink mass in every row of each detected
text line. A score of 1.0 is identical; 0.90 is the visual-fidelity floor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_line_gate import detect_ink_bands, numbered_pages


def normalized_profile(
    pixels: bytes, width: int, top: int, bottom: int, ink_threshold: int
) -> list[float]:
    counts: list[int] = []
    for y in range(top, bottom + 1):
        count = 0
        for x in range(width):
            offset = (y * width + x) * 3
            count += int(pixel_is_ink(pixels, offset, ink_threshold))
        counts.append(count)
    total = sum(counts)
    if total == 0:
        return [0.0 for _ in counts]
    return [count / total for count in counts]


def profile_similarity(reference: list[float], candidate: list[float]) -> float:
    if len(reference) != len(candidate):
        raise RuntimeError("profile lengths differ")
    # Total-variation similarity. For normalized distributions L1 is in [0, 2].
    return 1.0 - 0.5 * sum(abs(a - b) for a, b in zip(reference, candidate))


def compare_page(
    reference_path: Path,
    candidate_path: Path,
    *,
    ink_threshold: int,
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
        raise RuntimeError("no rendered-ink line found")

    lines: list[dict[str, object]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        reference_profile = normalized_profile(reference, width, top, bottom, ink_threshold)
        candidate_profile = normalized_profile(candidate, width, top, bottom, ink_threshold)
        score = profile_similarity(reference_profile, candidate_profile)
        lines.append(
            {
                "line": line_index,
                "top": top,
                "bottom": bottom,
                "height": bottom - top + 1,
                "profile_similarity": score,
                "reference_profile": reference_profile,
                "candidate_profile": candidate_profile,
            }
        )

    return {
        "line_count": len(lines),
        "minimum_line_profile_similarity": min(
            float(line["profile_similarity"]) for line in lines
        ),
        "lines": lines,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--reference-prefix", default="system-harfbuzz")
    parser.add_argument("--candidate-prefix", default="harfrust")
    parser.add_argument("--ink-threshold", type=int, default=250)
    parser.add_argument("--max-blank-row-gap", type=int, default=2)
    parser.add_argument("--min-line-profile-similarity", type=float, default=0.90)
    parser.add_argument("--expect-below", type=float, default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
                **compare_page(
                    reference,
                    candidate,
                    ink_threshold=args.ink_threshold,
                    max_blank_row_gap=args.max_blank_row_gap,
                ),
            }
        )

    minimum_similarity = min(
        float(page["minimum_line_profile_similarity"]) for page in pages
    )
    result = {
        "reference_prefix": args.reference_prefix,
        "candidate_prefix": args.candidate_prefix,
        "ink_threshold": args.ink_threshold,
        "minimum_line_profile_similarity": minimum_similarity,
        "pages": pages,
    }
    calibration = args.expect_below is not None
    output = Path(args.output) if args.output else directory / (
        "vertical-profile-calibration.json" if calibration else "vertical-profile-results.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    if calibration:
        if minimum_similarity >= args.expect_below:
            print(
                "vertical-profile calibration failed to detect deliberate mark shift: "
                f"minimum={minimum_similarity:.6f}, expected < {args.expect_below:.6f}",
                file=sys.stderr,
            )
            return 1
        return 0

    if minimum_similarity < args.min_line_profile_similarity:
        print(
            f"minimum line vertical-profile similarity {minimum_similarity:.6f} is below "
            f"required {args.min_line_profile_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
