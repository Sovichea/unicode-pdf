#!/usr/bin/env python3
"""Raster-compare Khmer output from HarfRust against system HarfBuzz.

This is a visual-fidelity experiment. It intentionally compares rendered pixels,
not PDF bytes or extracted Unicode, so semantic improvements cannot hide a visual
regression.
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


def render_first_page(pdf: Path, output_prefix: Path, dpi: int) -> Path:
    run(
        [
            "pdftoppm",
            "-f",
            "1",
            "-singlefile",
            "-r",
            str(dpi),
            str(pdf),
            str(output_prefix),
        ]
    )
    output = output_prefix.with_suffix(".ppm")
    if not output.is_file():
        raise RuntimeError(f"pdftoppm did not create {output}")
    return output


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


def compare_rasters(reference: Path, candidate: Path) -> dict[str, float | int]:
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
    for offset in range(0, len(ref), 3):
        pixel_changed = False
        for channel in range(3):
            delta = abs(ref[offset + channel] - cand[offset + channel])
            absolute_error += delta
            max_channel_error = max(max_channel_error, delta)
            pixel_changed |= delta != 0
        changed_pixels += int(pixel_changed)

    channel_count = len(ref)
    pixel_count = ref_width * ref_height
    similarity = 1.0 - absolute_error / (channel_count * 255)
    return {
        "width": ref_width,
        "height": ref_height,
        "pixel_count": pixel_count,
        "changed_pixels": changed_pixels,
        "changed_pixel_fraction": changed_pixels / pixel_count,
        "mean_absolute_channel_error": absolute_error / channel_count,
        "max_channel_error": max_channel_error,
        "similarity": similarity,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="fixtures/khmer.txt")
    parser.add_argument("--font-family", default="Noto Sans Khmer")
    parser.add_argument("--out", default="target/visual-backend-parity")
    parser.add_argument("--dpi", type=int, default=144)
    parser.add_argument("--min-similarity", type=float, default=0.995)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = Path(args.fixture)
    if not fixture.is_file():
        raise RuntimeError(f"fixture does not exist: {fixture}")

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

    reference_ppm = render_first_page(reference_pdf, out / "system-harfbuzz", args.dpi)
    candidate_ppm = render_first_page(candidate_pdf, out / "harfrust", args.dpi)
    metrics = compare_rasters(reference_ppm, candidate_ppm)
    result = {
        "fixture": str(fixture),
        "font": str(font),
        "font_family": args.font_family,
        "dpi": args.dpi,
        "reference_backend": "system-harfbuzz+unicode-bidi",
        "candidate_backend": "harfrust+unicode-bidi",
        "minimum_similarity": args.min_similarity,
        **metrics,
    }
    (out / "results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    if float(metrics["similarity"]) < args.min_similarity:
        print(
            f"visual similarity {metrics['similarity']:.6f} is below "
            f"required {args.min_similarity:.6f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
