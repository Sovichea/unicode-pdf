#!/usr/bin/env python3
"""Raster-compare Khmer output from HarfRust against system HarfBuzz.

This is a visual-fidelity experiment. It intentionally compares rendered pixels,
not PDF bytes or extracted Unicode, so semantic improvements cannot hide a visual
regression. In addition to whole-page similarity, it compares the rendered ink
mask so a glyph-position regression cannot be diluted by blank page area.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env)


def capture(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def resolve_font(family: str) -> Path:
    match = capture(["fc-match", "-f", "%{file}\n", family]).splitlines()
    if not match:
        raise RuntimeError(f"fontconfig did not resolve {family!r}")
    path = Path(match[0])
    if not path.is_file():
        raise RuntimeError(f"resolved font does not exist: {path}")
    return path


def emit_pdf(
    *,
    fixture: Path,
    font: Path,
    output: Path,
    target_dir: Path,
    features: str | None,
) -> None:
    command = [
        "cargo",
        "run",
        "--quiet",
        "-p",
        "unicode-pdf-cli",
    ]
    if features is not None:
        command.extend(["--no-default-features", "--features", features])
    command.extend(["--", "emit-pdf", str(font), str(fixture), str(output)])
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target_dir)
    run(command, env=env)


def render_pages(pdf: Path, output_prefix: Path, dpi: int) -> list[Path]:
    run(["pdftoppm", "-r", str(dpi), str(pdf), str(output_prefix)])
    pages = sorted(
        output_prefix.parent.glob(f"{output_prefix.name}-*.ppm"),
        key=lambda path: int(path.stem.rsplit("-", 1)[1]),
    )
    if not pages:
        raise RuntimeError(f"pdftoppm did not create pages for {pdf}")
    return pages


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    with path.open("rb") as handle:
        magic = handle.readline().strip()
        if magic != b"P6":
            raise RuntimeError(f"unsupported PPM format {magic!r} in {path}")

        tokens: list[bytes] = []
        while len(tokens) < 3:
            line = handle.readline()
            if not line:
                raise RuntimeError(f"truncated PPM header in {path}")
            line = line.split(b"#", 1)[0]
            tokens.extend(line.split())

        width, height, max_value = map(int, tokens[:3])
        if max_value != 255:
            raise RuntimeError(f"expected 8-bit PPM, got max value {max_value}")
        pixels = handle.read()

    expected = width * height * 3
    if len(pixels) != expected:
        raise RuntimeError(
            f"unexpected raster size in {path}: {len(pixels)} bytes, expected {expected}"
        )
    return width, height, pixels


def pixel_is_ink(pixels: bytes, offset: int, threshold: int) -> bool:
    return min(pixels[offset : offset + 3]) < threshold


def compare_rasters(
    reference: Path, candidate: Path, *, ink_threshold: int
) -> dict[str, float | int]:
    ref_width, ref_height, ref = read_ppm(reference)
    cand_width, cand_height, cand = read_ppm(candidate)
    if (ref_width, ref_height) != (cand_width, cand_height):
        raise RuntimeError(
            "render dimensions differ: "
            f"reference={ref_width}x{ref_height}, candidate={cand_width}x{cand_height}"
        )

    absolute_error = 0
    max_channel_error = 0
    changed_pixels = 0
    reference_ink_pixels = 0
    candidate_ink_pixels = 0
    ink_intersection_pixels = 0
    ink_union_pixels = 0
    ink_absolute_error = 0

    for offset in range(0, len(ref), 3):
        ref_ink = pixel_is_ink(ref, offset, ink_threshold)
        cand_ink = pixel_is_ink(cand, offset, ink_threshold)
        reference_ink_pixels += int(ref_ink)
        candidate_ink_pixels += int(cand_ink)
        ink_intersection_pixels += int(ref_ink and cand_ink)
        ink_union_pixels += int(ref_ink or cand_ink)

        pixel_changed = False
        pixel_error = 0
        for channel in range(3):
            delta = abs(ref[offset + channel] - cand[offset + channel])
            absolute_error += delta
            pixel_error += delta
            max_channel_error = max(max_channel_error, delta)
            pixel_changed |= delta != 0
        if ref_ink or cand_ink:
            ink_absolute_error += pixel_error
        changed_pixels += int(pixel_changed)

    channel_count = len(ref)
    pixel_count = ref_width * ref_height
    similarity = 1.0 - absolute_error / (channel_count * 255)
    ink_iou = (
        ink_intersection_pixels / ink_union_pixels if ink_union_pixels else 1.0
    )
    ink_similarity = (
        1.0 - ink_absolute_error / (ink_union_pixels * 3 * 255)
        if ink_union_pixels
        else 1.0
    )
    return {
        "width": ref_width,
        "height": ref_height,
        "pixel_count": pixel_count,
        "changed_pixels": changed_pixels,
        "changed_pixel_fraction": changed_pixels / pixel_count,
        "mean_absolute_channel_error": absolute_error / channel_count,
        "max_channel_error": max_channel_error,
        "similarity": similarity,
        "reference_ink_pixels": reference_ink_pixels,
        "candidate_ink_pixels": candidate_ink_pixels,
        "ink_intersection_pixels": ink_intersection_pixels,
        "ink_union_pixels": ink_union_pixels,
        "ink_iou": ink_iou,
        "ink_similarity": ink_similarity,
        "absolute_channel_error": absolute_error,
        "channel_count": channel_count,
        "ink_absolute_channel_error": ink_absolute_error,
    }


def compare_page_sets(
    reference_pages: list[Path],
    candidate_pages: list[Path],
    *,
    ink_threshold: int,
) -> tuple[list[dict[str, float | int]], dict[str, float | int]]:
    if len(reference_pages) != len(candidate_pages):
        raise RuntimeError(
            "rendered page counts differ: "
            f"reference={len(reference_pages)}, candidate={len(candidate_pages)}"
        )

    page_metrics: list[dict[str, float | int]] = []
    total_absolute_error = 0
    total_channel_count = 0
    total_pixels = 0
    total_changed_pixels = 0
    total_reference_ink = 0
    total_candidate_ink = 0
    total_ink_intersection = 0
    total_ink_union = 0
    total_ink_absolute_error = 0
    max_channel_error = 0

    hidden = {
        "absolute_channel_error",
        "channel_count",
        "ink_absolute_channel_error",
    }
    for page_number, (reference, candidate) in enumerate(
        zip(reference_pages, candidate_pages), start=1
    ):
        metrics = compare_rasters(
            reference, candidate, ink_threshold=ink_threshold
        )
        page_metrics.append(
            {
                "page": page_number,
                **{key: value for key, value in metrics.items() if key not in hidden},
            }
        )
        total_absolute_error += int(metrics["absolute_channel_error"])
        total_channel_count += int(metrics["channel_count"])
        total_pixels += int(metrics["pixel_count"])
        total_changed_pixels += int(metrics["changed_pixels"])
        total_reference_ink += int(metrics["reference_ink_pixels"])
        total_candidate_ink += int(metrics["candidate_ink_pixels"])
        total_ink_intersection += int(metrics["ink_intersection_pixels"])
        total_ink_union += int(metrics["ink_union_pixels"])
        total_ink_absolute_error += int(metrics["ink_absolute_channel_error"])
        max_channel_error = max(max_channel_error, int(metrics["max_channel_error"]))

    similarity = 1.0 - total_absolute_error / (total_channel_count * 255)
    ink_iou = total_ink_intersection / total_ink_union if total_ink_union else 1.0
    ink_similarity = (
        1.0 - total_ink_absolute_error / (total_ink_union * 3 * 255)
        if total_ink_union
        else 1.0
    )
    summary = {
        "page_count": len(page_metrics),
        "pixel_count": total_pixels,
        "changed_pixels": total_changed_pixels,
        "changed_pixel_fraction": total_changed_pixels / total_pixels,
        "mean_absolute_channel_error": total_absolute_error / total_channel_count,
        "max_channel_error": max_channel_error,
        "similarity": similarity,
        "minimum_page_similarity": min(float(page["similarity"]) for page in page_metrics),
        "reference_ink_pixels": total_reference_ink,
        "candidate_ink_pixels": total_candidate_ink,
        "ink_intersection_pixels": total_ink_intersection,
        "ink_union_pixels": total_ink_union,
        "ink_iou": ink_iou,
        "ink_similarity": ink_similarity,
        "minimum_page_ink_iou": min(float(page["ink_iou"]) for page in page_metrics),
        "minimum_page_ink_similarity": min(
            float(page["ink_similarity"]) for page in page_metrics
        ),
    }
    return page_metrics, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="fixtures/khmer.txt")
    parser.add_argument("--font-family", default="Noto Sans Khmer")
    parser.add_argument("--out", default="target/visual-backend-parity")
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--min-similarity", type=float, default=0.995)
    parser.add_argument("--min-ink-iou", type=float, default=0.90)
    parser.add_argument("--ink-threshold", type=int, default=250)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = Path(args.fixture)
    if not fixture.is_file():
        raise RuntimeError(f"fixture does not exist: {fixture}")
    if not 0 <= args.ink_threshold <= 255:
        raise RuntimeError("--ink-threshold must be between 0 and 255")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    font = resolve_font(args.font_family)

    reference_pdf = out / "system-harfbuzz.pdf"
    candidate_pdf = out / "harfrust.pdf"
    emit_pdf(
        fixture=fixture,
        font=font,
        output=reference_pdf,
        target_dir=out / "system-target",
        features="system-harfbuzz,unicode-bidi",
    )
    emit_pdf(
        fixture=fixture,
        font=font,
        output=candidate_pdf,
        target_dir=out / "pure-target",
        features=None,
    )

    reference_pages = render_pages(reference_pdf, out / "system-harfbuzz", args.dpi)
    candidate_pages = render_pages(candidate_pdf, out / "harfrust", args.dpi)
    pages, metrics = compare_page_sets(
        reference_pages, candidate_pages, ink_threshold=args.ink_threshold
    )
    result = {
        "fixture": str(fixture),
        "font": str(font),
        "font_family": args.font_family,
        "dpi": args.dpi,
        "reference_backend": "system-harfbuzz+unicode-bidi",
        "candidate_backend": "harfrust+unicode-bidi",
        "minimum_similarity": args.min_similarity,
        "minimum_ink_iou": args.min_ink_iou,
        "ink_threshold": args.ink_threshold,
        "pages": pages,
        **metrics,
    }
    (out / "results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    failed = False
    if float(metrics["minimum_page_similarity"]) < args.min_similarity:
        print(
            "minimum per-page visual similarity "
            f"{metrics['minimum_page_similarity']:.6f} is below "
            f"required {args.min_similarity:.6f}",
            file=sys.stderr,
        )
        failed = True
    if float(metrics["minimum_page_ink_iou"]) < args.min_ink_iou:
        print(
            "minimum per-page rendered-ink IoU "
            f"{metrics['minimum_page_ink_iou']:.6f} is below "
            f"required {args.min_ink_iou:.6f}",
            file=sys.stderr,
        )
        failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
