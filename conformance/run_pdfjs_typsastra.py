#!/usr/bin/env python3
"""Validate the Typsastra PDF.js logical-text compatibility mode."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_FAMILIES = [
    "Noto Sans",
    "Noto Sans Khmer",
    "Noto Sans Arabic",
    "Noto Sans Devanagari",
]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def font_for_family(family: str) -> str:
    result = run(["fc-match", "-f", "%{file}\\n", family]).stdout.strip().splitlines()
    if not result or not Path(result[0]).is_file():
        raise RuntimeError(f"missing font family: {family}")
    return result[0]


def source_byte_range(source: str, needle: str) -> tuple[int, int]:
    first = source.find(needle)
    if first < 0:
        raise RuntimeError(f"needle not found in source: {needle!r}")
    if source.find(needle, first + 1) >= 0:
        raise RuntimeError(f"needle must be unique in fixture: {needle!r}")
    start = len(source[:first].encode("utf-8"))
    return start, start + len(needle.encode("utf-8"))


def expected_rect(geometry: dict, source_range: tuple[int, int]) -> list[float]:
    start, end = source_range
    units = [
        unit
        for unit in geometry["units"]
        if unit["source_start"] < end and unit["source_end"] > start
    ]
    if not units:
        raise RuntimeError(f"no compiler geometry for source bytes {start}..{end}")
    pages = {unit["page"] for unit in units}
    if len(pages) != 1:
        raise RuntimeError("current PDF.js DOM selection fixture must stay on one page")
    page_height = float(geometry["page_height"])
    return [
        min(unit["x0"] for unit in units),
        page_height - max(unit["y1"] for unit in units),
        max(unit["x1"] for unit in units),
        page_height - min(unit["y0"] for unit in units),
    ]


def intersection_coverage(expected: list[float], actual: list[float] | None) -> float:
    if not actual:
        return 0.0
    x0 = max(expected[0], actual[0])
    y0 = max(expected[1], actual[1])
    x1 = min(expected[2], actual[2])
    y1 = min(expected[3], actual[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    area = max((expected[2] - expected[0]) * (expected[3] - expected[1]), 1e-9)
    return min(1.0, intersection / area)


def horizontal_edge_error(expected: list[float], actual: list[float] | None) -> float:
    if not actual:
        return float("inf")
    return max(abs(expected[0] - actual[0]), abs(expected[2] - actual[2]))


def evaluate(source: str, geometry: dict, probe: dict) -> dict:
    by_name = {case["name"]: case for case in probe["cases"]}
    output: dict[str, dict] = {}
    for case in json.loads((ROOT / "conformance/pdfjs_typsastra_cases.json").read_text())["cases"]:
        observed = by_name[case["name"]]
        expected = expected_rect(geometry, source_byte_range(source, case["needle"]))
        actual = observed.get("union")
        output[case["name"]] = {
            "needle": case["needle"],
            "copied": observed.get("copied"),
            "copy_exact": observed.get("copied") == case["needle"],
            "expected_rect": expected,
            "actual_rect": actual,
            "geometry_coverage": intersection_coverage(expected, actual),
            "horizontal_edge_error_px": horizontal_edge_error(expected, actual),
            "rects": observed.get("rects", []),
        }
    return output


def render_page(pdf: Path, png: Path) -> None:
    if not shutil.which("pdftoppm"):
        return
    prefix = png.with_suffix("")
    run(["pdftoppm", "-f", "1", "-singlefile", "-r", "72", "-png", str(pdf), str(prefix)])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdfjs-dist", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "target/pdfjs-typsastra")
    parser.add_argument("--browser", choices=["chromium", "firefox"], default="chromium")
    parser.add_argument("--browser-executable")
    parser.add_argument("--check-baseline", action="store_true")
    parser.add_argument("--screenshots", action="store_true")
    args = parser.parse_args()

    config = json.loads((ROOT / "conformance/pdfjs_typsastra_cases.json").read_text())
    baseline = json.loads((ROOT / "conformance/pdfjs_typsastra_baseline.json").read_text())
    fixture = ROOT / config["fixture"]
    source = fixture.read_text(encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)

    fonts = [font_for_family(family) for family in FONT_FAMILIES]
    pdf = args.out / "fixture.pdf"
    geometry_path = args.out / "geometry.json"
    run(["cargo", "run", "-q", "-p", "unicode-pdf-cli", "--", "emit-layout-pdf", str(fixture), str(pdf), *fonts])
    run(["cargo", "run", "-q", "-p", "unicode-pdf-cli", "--", "dump-layout-geometry", str(fixture), str(geometry_path), *fonts])
    geometry = json.loads(geometry_path.read_text(encoding="utf-8"))

    stock_dist = args.pdfjs_dist.resolve()
    stock_main = stock_dist / "build/pdf.mjs" if stock_dist.is_dir() else stock_dist
    stock_dist = stock_main.parents[1]
    patched_dist = args.out / "pdfjs-dist-typsastra"
    run([
        sys.executable,
        str(ROOT / "integrations/pdfjs/apply_typsastra_patch.py"),
        "--input",
        str(stock_dist),
        "--output",
        str(patched_dist),
    ])
    patched_main = patched_dist / "build/pdf.mjs"

    stock_content = args.out / "stock.text-content.json"
    patched_content = args.out / "patched.text-content.json"
    run(["node", str(ROOT / "conformance/pdfjs_text_content.mjs"), str(stock_main), str(pdf), str(stock_content), "stock"])
    run(["node", str(ROOT / "conformance/pdfjs_text_content.mjs"), str(patched_main), str(pdf), str(patched_content), "logical"])

    page_png = args.out / "page.png"
    if args.screenshots:
        render_page(pdf, page_png)
    if not page_png.is_file():
        page_png = None

    probes = {}
    for label, main_path, content_path in [
        ("stock", stock_main, stock_content),
        ("patched", patched_main, patched_content),
    ]:
        probe_path = args.out / f"{label}.{args.browser}.probe.json"
        screenshot_dir = args.out / f"screenshots-{label}-{args.browser}" if args.screenshots else None
        cmd = [
            sys.executable,
            str(ROOT / "conformance/pdfjs_textlayer_probe.py"),
            "--pdfjs-main",
            str(main_path),
            "--content",
            str(content_path),
            "--cases",
            str(ROOT / "conformance/pdfjs_typsastra_cases.json"),
            "--out",
            str(probe_path),
            "--page-width",
            str(geometry["page_width"]),
            "--page-height",
            str(geometry["page_height"]),
            "--browser",
            args.browser,
        ]
        if args.browser_executable:
            cmd += ["--executable", args.browser_executable]
        if screenshot_dir:
            cmd += ["--screenshot-dir", str(screenshot_dir)]
        if page_png:
            cmd += ["--page-image", str(page_png)]
        run(cmd)
        probes[label] = json.loads(probe_path.read_text(encoding="utf-8"))

    results = {
        "browser": probes["patched"]["browser"],
        "browser_version": probes["patched"]["browser_version"],
        "pdfjs_version": probes["patched"]["pdfjs_version"],
        "stock": evaluate(source, geometry, probes["stock"]),
        "patched": evaluate(source, geometry, probes["patched"]),
    }
    failures = []
    target = baseline["patched"]
    for name, case in results["patched"].items():
        case_target = {**target, **target.get("per_case", {}).get(name, {})}
        if case_target["require_exact_copy"] and not case["copy_exact"]:
            failures.append(f"{name}: copied text differs from source")
        if case["geometry_coverage"] < case_target["minimum_geometry_coverage"]:
            failures.append(
                f"{name}: geometry coverage {case['geometry_coverage']:.4f} < {case_target['minimum_geometry_coverage']:.4f}"
            )
        if case["horizontal_edge_error_px"] > case_target["maximum_horizontal_edge_error_px"]:
            failures.append(
                f"{name}: edge error {case['horizontal_edge_error_px']:.3f}px > {case_target['maximum_horizontal_edge_error_px']:.3f}px"
            )

    result_path = args.out / "results.json"
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = [
        "# Typsastra PDF.js Selection Compatibility",
        "",
        f"Browser: {results['browser']} {results['browser_version']}",
        f"PDF.js: {results['pdfjs_version']}",
        "",
        "| Selection | Stock geometry | Patched geometry | Stock edge error | Patched edge error | Patched copy |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for case in config["cases"]:
        name = case["name"]
        stock = results["stock"][name]
        patched = results["patched"][name]
        report.append(
            f"| {name} | {100 * stock['geometry_coverage']:.2f}% | {100 * patched['geometry_coverage']:.2f}% | "
            f"{stock['horizontal_edge_error_px']:.2f}px | {patched['horizontal_edge_error_px']:.2f}px | "
            f"{'exact' if patched['copy_exact'] else 'FAIL'} |"
        )
    report.append("")
    improvements = sum(
        results["patched"][name]["geometry_coverage"]
        > results["stock"][name]["geometry_coverage"] + 0.01
        for name in results["patched"]
    )
    report.append(f"Selections with >1 percentage point geometry improvement: {improvements}/{len(results['patched'])}.")
    report_path = args.out / "RESULTS.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))

    if args.check_baseline and failures:
        print("\nBaseline failures:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
