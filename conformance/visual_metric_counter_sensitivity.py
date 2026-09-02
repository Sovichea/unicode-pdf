#!/usr/bin/env python3
"""Inject a filled Khmer glyph counter while preserving outer ink topology."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_backend_parity import pixel_is_ink, read_ppm
from visual_counter_window_gate import enclosed_white_count
from visual_line_gate import detect_ink_bands, numbered_pages
from visual_metric_scale_sensitivity import write_ppm


def enclosed_white_components(pixels: bytes, width: int, top: int, bottom: int,
                              left: int, right: int, threshold: int) -> list[list[tuple[int, int]]]:
    white = {(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)
             if not pixel_is_ink(pixels, (y * width + x) * 3, threshold)}
    holes: list[list[tuple[int, int]]] = []
    while white:
        seed = white.pop()
        stack = [seed]
        component = [seed]
        touches_border = seed[0] in (left, right) or seed[1] in (top, bottom)
        while stack:
            x, y = stack.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbor = (x + dx, y + dy)
                if neighbor in white:
                    white.remove(neighbor)
                    stack.append(neighbor)
                    component.append(neighbor)
                    if neighbor[0] in (left, right) or neighbor[1] in (top, bottom):
                        touches_border = True
        if not touches_border:
            holes.append(component)
    return holes


def ink_component_count(pixels: bytes, width: int, top: int, bottom: int,
                        left: int, right: int, threshold: int) -> int:
    points = {(x, y) for y in range(top, bottom + 1) for x in range(left, right + 1)
              if pixel_is_ink(pixels, (y * width + x) * 3, threshold)}
    count = 0
    while points:
        seed = points.pop()
        stack = [seed]
        count += 1
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
    return count


def fill_one_counter(source: Path, destination: Path, *, threshold: int,
                     min_hole_pixels: int, max_hole_pixels: int,
                     max_blank_row_gap: int) -> dict[str, object]:
    width, height, pixels = read_ppm(source)
    bands = detect_ink_bands(
        width, height, pixels, pixels, ink_threshold=threshold,
        max_blank_row_gap=max_blank_row_gap,
    )
    choices: list[tuple[int, int, list[tuple[int, int]], tuple[int, int, int, int]]] = []
    for line_index, (top, bottom) in enumerate(bands, start=1):
        xs = [x for y in range(top, bottom + 1) for x in range(width)
              if pixel_is_ink(pixels, (y * width + x) * 3, threshold)]
        if not xs:
            continue
        left, right = min(xs), max(xs)
        for hole in enclosed_white_components(pixels, width, top, bottom, left, right, threshold):
            if min_hole_pixels <= len(hole) <= max_hole_pixels:
                choices.append((len(hole), line_index, hole, (top, bottom, left, right)))
    if not choices:
        raise RuntimeError("no enclosed counter met the configured size range")

    for _, line_index, hole, (top, bottom, left, right) in sorted(choices, reverse=True):
        before_components = ink_component_count(pixels, width, top, bottom, left, right, threshold)
        output = bytearray(pixels)
        for x, y in hole:
            offset = (y * width + x) * 3
            output[offset:offset + 3] = b"\x00\x00\x00"
        after = bytes(output)
        after_components = ink_component_count(after, width, top, bottom, left, right, threshold)
        if before_components != after_components:
            continue
        write_ppm(destination, width, height, after)
        xs = [x for x, _ in hole]
        ys = [y for _, y in hole]
        return {
            "line": line_index,
            "filled_pixels": len(hole),
            "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "ink_component_count_before": before_components,
            "ink_component_count_after": after_components,
        }
    raise RuntimeError("no eligible counter preserved connected ink topology after filling")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--ink-threshold", type=int, default=250)
    p.add_argument("--min-hole-pixels", type=int, default=4)
    p.add_argument("--max-hole-pixels", type=int, default=12)
    p.add_argument("--max-blank-row-gap", type=int, default=2)
    return p.parse_args()


def main() -> int:
    a = parse_args()
    if a.min_hole_pixels <= 0 or a.max_hole_pixels < a.min_hole_pixels:
        raise RuntimeError("hole size bounds are invalid")
    directory = Path(a.dir)
    pages = numbered_pages(directory, "system-harfbuzz")
    if not pages:
        raise RuntimeError(f"no system-harfbuzz rasters found in {directory}")

    label = f"h{a.min_hole_pixels}-{a.max_hole_pixels}"
    output_dir = directory / f"sensitivity-counter-{label}"
    prefix = f"system-harfbuzz-counter-{label}"
    results = []
    for page_number, page in enumerate(pages, start=1):
        destination = output_dir / f"{prefix}-{page_number}.ppm"
        results.append({
            "page": page_number,
            **fill_one_counter(
                page,
                destination,
                threshold=a.ink_threshold,
                min_hole_pixels=a.min_hole_pixels,
                max_hole_pixels=a.max_hole_pixels,
                max_blank_row_gap=a.max_blank_row_gap,
            ),
        })

    result = {
        "min_hole_pixels": a.min_hole_pixels,
        "max_hole_pixels": a.max_hole_pixels,
        "candidate_prefix": f"sensitivity-counter-{label}/{prefix}",
        "pages": results,
    }
    output = directory / f"sensitivity-results-counter-{label}.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
