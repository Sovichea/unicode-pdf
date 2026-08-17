#!/usr/bin/env python3
"""Cross-reader Unicode extraction conformance harness for unicode-pdf."""

from __future__ import annotations

import argparse
import difflib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unicodedata
from typing import Any, Callable

from cargo_backend import cargo_cli_prefix

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "conformance" / "cases.json"
DEFAULT_BASELINE = ROOT / "conformance" / "baseline.json"
PDFJS_ADAPTER = ROOT / "conformance" / "adapters" / "pdfjs.mjs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate unicode-pdf fixtures and compare text extraction across readers."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--out", type=Path, default=ROOT / "target" / "conformance")
    parser.add_argument(
        "--readers",
        default="auto",
        help="comma-separated readers: poppler,mupdf,pdfium,pdfjs; default: auto",
    )
    parser.add_argument(
        "--require",
        default="",
        help="comma-separated readers that must be available",
    )
    parser.add_argument(
        "--pdfjs-dist",
        type=Path,
        default=Path(os.environ["PDFJS_DIST"]) if os.environ.get("PDFJS_DIST") else None,
        help="PDF.js dist directory or build/pdf.mjs path (also PDFJS_DIST)",
    )
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="reuse PDFs already present in --out",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="fail if a baseline pass becomes a failure",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="selected_cases",
        help="run only a named case; repeatable",
    )
    parser.add_argument(
        "--json",
        type=Path,
        dest="json_path",
        help="write machine-readable results; default: <out>/results.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        dest="markdown_path",
        help="write Markdown summary; default: <out>/RESULTS.md",
    )
    return parser.parse_args()


def run_command(args: list[str], *, cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        stdout = result.stdout.decode("utf-8", "replace").strip()
        stderr = result.stderr.decode("utf-8", "replace").strip()
        command = " ".join(map(str, args))
        details = [f"command failed with exit code {result.returncode}: {command}"]
        if stdout:
            details.append(f"stdout:\n{stdout}")
        if stderr:
            details.append(f"stderr:\n{stderr}")
        raise RuntimeError("\n".join(details))
    return result


def command_version(command: str, args: list[str]) -> str:
    try:
        result = run_command([command, *args])
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    text = (result.stdout + result.stderr).decode("utf-8", "replace").strip()
    return text.splitlines()[0] if text else "unknown"


def resolve_pdfjs_module(path: Path | None) -> Path | None:
    if path is None:
        return None
    path = path.expanduser().resolve()
    candidates = [path]
    if path.is_dir():
        candidates = [path / "build" / "pdf.mjs", path / "pdf.mjs"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def resolve_font_spec(spec: dict[str, Any]) -> Path:
    env_name = spec["font_env"]
    if os.environ.get(env_name):
        path = Path(os.environ[env_name]).expanduser()
        if path.is_file():
            return path.resolve()
        raise RuntimeError(f"{env_name} points to missing font: {path}")

    fc_match = shutil.which("fc-match")
    if fc_match:
        result = run_command([fc_match, "-f", "%{file}\n", spec["font_family"]])
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            path = Path(line.strip())
            if path.is_file():
                return path.resolve()

    raise RuntimeError(
        f"unable to resolve font {spec['font_family']!r}; set {env_name} to a glyf-based TrueType font"
    )


def resolve_font(case: dict[str, Any]) -> Path:
    return resolve_font_spec(case)


def generate_pdf(case: dict[str, Any], output: Path) -> dict[str, Any]:
    fixture = (ROOT / case["fixture"]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if case.get("generator") == "layout":
        fonts = [resolve_font_spec(spec) for spec in case["fonts"]]
        if case.get("breaks"):
            breaks = (ROOT / case["breaks"]).resolve()
            command = [
                *cargo_cli_prefix(),
                "emit-layout-pdf-breaks", str(fixture), str(breaks), str(output), *map(str, fonts),
            ]
        else:
            command = [
                *cargo_cli_prefix(),
                "emit-layout-pdf", str(fixture), str(output), *map(str, fonts),
            ]
        result = run_command(command)
        metadata: dict[str, Any] = {"fonts": [str(font) for font in fonts]}
        if case.get("breaks"):
            metadata["breaks"] = str((ROOT / case["breaks"]).resolve())
    else:
        font = resolve_font(case)
        result = run_command(
            [
                *cargo_cli_prefix(),
                "emit-pdf",
                str(font),
                str(fixture),
                str(output),
            ]
        )
        metadata = {"font": str(font)}
    if shutil.which("gs"):
        run_command(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=nullpage", str(output)])
    metadata["generator_stdout"] = result.stdout.decode("utf-8", "replace")
    return metadata


def normalize_selection_text(text: str) -> str:
    """Normalize only reader/page transport artifacts, never Unicode semantics.

    - CRLF and CR become LF.
    - form-feed page separators emitted by command-line extractors are removed.
    - terminal LF characters are ignored because selecting a complete page may or may
      not include a final paragraph terminator depending on the reader.

    Internal whitespace, BiDi controls, normalization form, combining-mark order,
    and all other Unicode scalars are preserved exactly.
    """

    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "").rstrip("\n")


def scalar_context(text: str, index: int, radius: int = 5) -> list[dict[str, Any]]:
    chars = list(text)
    start = max(0, index - radius)
    end = min(len(chars), index + radius + 1)
    return [
        {
            "index": i,
            "char": chars[i],
            "codepoint": f"U+{ord(chars[i]):04X}",
            "name": unicodedata.name(chars[i], "<unnamed>"),
        }
        for i in range(start, end)
    ]


def first_mismatch(expected: str, actual: str) -> dict[str, Any] | None:
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            return {
                "index": index,
                "expected": scalar_context(expected, index),
                "actual": scalar_context(actual, index),
            }
    if len(expected) != len(actual):
        index = min(len(expected), len(actual))
        return {
            "index": index,
            "expected": scalar_context(expected, min(index, max(0, len(expected) - 1))) if expected else [],
            "actual": scalar_context(actual, min(index, max(0, len(actual) - 1))) if actual else [],
        }
    return None


def compare_text(expected_raw: str, actual_raw: str) -> dict[str, Any]:
    expected = normalize_selection_text(expected_raw)
    actual = normalize_selection_text(actual_raw)
    matcher = difflib.SequenceMatcher(a=list(expected), b=list(actual), autojunk=False)
    return {
        "raw_exact": actual_raw == expected_raw,
        "selection_exact": actual == expected,
        "nfc_exact": unicodedata.normalize("NFC", actual) == unicodedata.normalize("NFC", expected),
        "similarity": round(matcher.ratio(), 6),
        "expected_scalars": len(expected),
        "actual_scalars": len(actual),
        "first_mismatch": first_mismatch(expected, actual),
        "actual_text": actual_raw,
    }


def analyze_generated_character_pages(
    expected_raw: str,
    pages: list[list[tuple[str, bool]]],
) -> dict[str, Any]:
    """Classify reader-generated characters without repairing reader output.

    This is intentionally diagnostic only. The normal conformance comparison still
    sees every generated character exactly as the reader exposes it. The filtered
    comparison answers a narrower question: would the text be exact if *only*
    characters explicitly marked by the reader as generated CR/LF were omitted?
    """

    page_texts: list[str] = []
    filtered_page_texts: list[str] = []
    generated_count = 0
    generated_line_break_count = 0
    generated_other_count = 0
    generated_crlf_pairs = 0
    examples: list[dict[str, Any]] = []

    for page_index, page in enumerate(pages):
        page_texts.append("".join(char for char, _ in page))
        filtered_page_texts.append(
            "".join(char for char, generated in page if not (generated and char in "\r\n"))
        )

        index = 0
        while index < len(page):
            char, generated = page[index]
            if generated:
                generated_count += 1
                if char in "\r\n":
                    generated_line_break_count += 1
                else:
                    generated_other_count += 1
                if len(examples) < 24:
                    examples.append(
                        {
                            "page": page_index,
                            "char_index": index,
                            "char": char,
                            "codepoint": f"U+{ord(char):04X}" if char else None,
                            "name": unicodedata.name(char, "<control-or-unnamed>") if char else "<none>",
                        }
                    )

            if (
                generated
                and char == "\r"
                and index + 1 < len(page)
                and page[index + 1][1]
                and page[index + 1][0] == "\n"
            ):
                generated_crlf_pairs += 1
            index += 1

    actual_raw = "\f".join(page_texts)
    without_generated_line_breaks_raw = "\f".join(filtered_page_texts)
    comparison = compare_text(expected_raw, actual_raw)
    filtered_comparison = compare_text(expected_raw, without_generated_line_breaks_raw)

    if comparison["selection_exact"]:
        classification = "exact"
    elif (
        generated_line_break_count > 0
        and generated_other_count == 0
        and filtered_comparison["selection_exact"]
    ):
        classification = "reader-generated-line-breaks-only"
    elif generated_count > 0:
        classification = "reader-generated-characters-plus-other-differences"
    else:
        classification = "no-reader-generated-characters"

    return {
        "classification": classification,
        "generated_char_count": generated_count,
        "generated_line_break_char_count": generated_line_break_count,
        "generated_crlf_pairs": generated_crlf_pairs,
        "generated_other_count": generated_other_count,
        "exact_after_removing_generated_linebreaks": filtered_comparison["selection_exact"],
        "filtered_similarity": filtered_comparison["similarity"],
        "generated_examples": examples,
    }


def inspect_pdfium_generated_characters(pdf: Path, expected_raw: str) -> dict[str, Any]:
    """Inspect PDFium's character stream using FPDFText_IsGenerated()."""

    pdfium = importlib.import_module("pypdfium2")
    raw = pdfium.raw
    document = pdfium.PdfDocument(str(pdf))
    pages: list[list[tuple[str, bool]]] = []
    stream_matches_text_range = True
    invalid_generated_statuses: list[dict[str, int]] = []
    try:
        for page_index, page in enumerate(document):
            text_page = page.get_textpage()
            try:
                count = int(raw.FPDFText_CountChars(text_page.raw))
                chars: list[tuple[str, bool]] = []
                for char_index in range(count):
                    codepoint = int(raw.FPDFText_GetUnicode(text_page.raw, char_index))
                    status = int(raw.FPDFText_IsGenerated(text_page.raw, char_index))
                    if status < 0:
                        invalid_generated_statuses.append(
                            {"page": page_index, "char_index": char_index, "status": status}
                        )
                    char = chr(codepoint) if codepoint else ""
                    chars.append((char, status > 0))
                if "".join(char for char, _ in chars) != text_page.get_text_range():
                    stream_matches_text_range = False
                pages.append(chars)
            finally:
                text_page.close()
            page.close()
    finally:
        document.close()

    diagnostics = analyze_generated_character_pages(expected_raw, pages)
    diagnostics.update(
        {
            "api": "FPDFText_IsGenerated",
            "character_stream_matches_get_text_range": stream_matches_text_range,
            "invalid_generated_statuses": invalid_generated_statuses,
        }
    )
    return diagnostics


def check_diagnostic_expectation(
    reader: str,
    case: str,
    diagnostics: dict[str, Any] | None,
    expectation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not expectation:
        return []
    if diagnostics is None:
        return [
            {
                "reader": reader,
                "case": case,
                "field": "diagnostics",
                "expected": expectation,
                "actual": None,
            }
        ]

    regressions: list[dict[str, Any]] = []
    for field in (
        "classification",
        "exact_after_removing_generated_linebreaks",
        "generated_other_count",
        "character_stream_matches_get_text_range",
    ):
        if field in expectation and diagnostics.get(field) != expectation[field]:
            regressions.append(
                {
                    "reader": reader,
                    "case": case,
                    "field": field,
                    "expected": expectation[field],
                    "actual": diagnostics.get(field),
                }
            )

    minimum = expectation.get("min_generated_line_break_char_count")
    if minimum is not None and diagnostics.get("generated_line_break_char_count", 0) < minimum:
        regressions.append(
            {
                "reader": reader,
                "case": case,
                "field": "generated_line_break_char_count",
                "expected": f">={minimum}",
                "actual": diagnostics.get("generated_line_break_char_count", 0),
            }
        )
    return regressions


def extract_poppler(pdf: Path) -> tuple[str, str]:
    result = run_command(["pdftotext", "-enc", "UTF-8", str(pdf), "-"])
    return result.stdout.decode("utf-8", "strict"), command_version("pdftotext", ["-v"])


def extract_mupdf(pdf: Path) -> tuple[str, str]:
    fitz = importlib.import_module("fitz")
    document = fitz.open(pdf)
    try:
        text = "\f".join(page.get_text("text", sort=False) for page in document)
    finally:
        document.close()
    version = getattr(fitz, "version", ("unknown", "unknown"))
    return text, f"PyMuPDF {version[0]} / MuPDF {version[1]}"


def extract_pdfium(pdf: Path) -> tuple[str, str]:
    pdfium = importlib.import_module("pypdfium2")
    document = pdfium.PdfDocument(str(pdf))
    pages: list[str] = []
    try:
        for page in document:
            text_page = page.get_textpage()
            try:
                pages.append(text_page.get_text_range())
            finally:
                text_page.close()
            page.close()
    finally:
        document.close()
    return "\f".join(pages), f"pypdfium2 {pdfium.PYPDFIUM_INFO} / PDFium {pdfium.PDFIUM_INFO}"


def extract_pdfjs(pdf: Path, pdfjs_module: Path) -> tuple[str, str]:
    result = run_command(["node", str(PDFJS_ADAPTER), str(pdfjs_module), str(pdf)])
    payload = json.loads(result.stdout.decode("utf-8", "strict"))
    return payload["text"], f"PDF.js {payload['version']}"


def reader_availability(pdfjs_module: Path | None) -> dict[str, tuple[bool, str]]:
    availability: dict[str, tuple[bool, str]] = {
        "poppler": (shutil.which("pdftotext") is not None, "pdftotext executable"),
        "mupdf": (importlib.util.find_spec("fitz") is not None, "Python module fitz/PyMuPDF"),
        "pdfium": (importlib.util.find_spec("pypdfium2") is not None, "Python module pypdfium2"),
        "pdfjs": (
            shutil.which("node") is not None and pdfjs_module is not None,
            "Node.js plus --pdfjs-dist/PDFJS_DIST",
        ),
    }
    return availability


def choose_readers(requested: str, availability: dict[str, tuple[bool, str]]) -> list[str]:
    if requested == "auto":
        return [name for name, (available, _) in availability.items() if available]
    names = [part.strip() for part in requested.split(",") if part.strip()]
    unknown = sorted(set(names) - set(availability))
    if unknown:
        raise RuntimeError(f"unknown readers: {', '.join(unknown)}")
    return names


def extractor_for(reader: str, pdfjs_module: Path | None) -> Callable[[Path], tuple[str, str]]:
    if reader == "poppler":
        return extract_poppler
    if reader == "mupdf":
        return extract_mupdf
    if reader == "pdfium":
        return extract_pdfium
    if reader == "pdfjs":
        if pdfjs_module is None:
            raise RuntimeError("PDF.js requested without a valid --pdfjs-dist/PDFJS_DIST")
        return lambda pdf: extract_pdfjs(pdf, pdfjs_module)
    raise AssertionError(reader)


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Cross-reader Unicode conformance results",
        "",
        "`PASS` means the extracted text matches the fixture after transport-only selection normalization. No NFC/NFKC, BiDi repair, whitespace repair, or combining-mark reordering is applied.",
        "",
        "| Reader | Version | Case | Selection exact | Raw exact | Similarity | Baseline |",
        "|---|---|---|---|---|---:|---|",
    ]
    for reader in report["readers"]:
        version = reader.get("version", "unavailable")
        if not reader["available"]:
            lines.append(f"| {reader['name']} | unavailable | - | SKIP | - | - | - |")
            continue
        for case in reader["cases"]:
            status = "PASS" if case["comparison"]["selection_exact"] else "FAIL"
            raw = "PASS" if case["comparison"]["raw_exact"] else "FAIL"
            baseline = case.get("baseline", "untracked")
            lines.append(
                f"| {reader['name']} | {version} | {case['name']} | {status} | {raw} | "
                f"{case['comparison']['similarity']:.3f} | {baseline} |"
            )

    lines.extend(["", "## Mismatches", ""])
    mismatch_count = 0
    for reader in report["readers"]:
        for case in reader.get("cases", []):
            comparison = case["comparison"]
            if comparison["selection_exact"]:
                continue
            mismatch_count += 1
            mismatch = comparison["first_mismatch"]
            lines.append(f"### {reader['name']} / {case['name']}")
            lines.append("")
            lines.append(f"Similarity: `{comparison['similarity']:.6f}`")
            if mismatch:
                lines.append(f"First differing scalar index: `{mismatch['index']}`")
            lines.append("")
            lines.append("Extracted text:")
            lines.append("")
            lines.append("```text")
            lines.append(normalize_selection_text(comparison["actual_text"]))
            lines.append("```")
            lines.append("")
    if mismatch_count == 0:
        lines.append("No mismatches.")
        lines.append("")

    pdfium_diagnostics = []
    for reader in report["readers"]:
        if reader["name"] != "pdfium":
            continue
        for case in reader.get("cases", []):
            diagnostics = case.get("reader_diagnostics")
            if diagnostics is not None:
                pdfium_diagnostics.append((case["name"], diagnostics))

    if pdfium_diagnostics:
        lines.extend(
            [
                "## PDFium generated-character diagnostics",
                "",
                "PDFium exposes whether each extracted character was synthesized by the reader through `FPDFText_IsGenerated()`. These diagnostics do not repair or normalize the conformance result; they classify why a strict mismatch occurred.",
                "",
                "| Case | Classification | Generated CR/LF chars | Generated CRLF pairs | Other generated chars | Exact after removing only generated CR/LF |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for name, diagnostics in pdfium_diagnostics:
            exact = "YES" if diagnostics["exact_after_removing_generated_linebreaks"] else "NO"
            lines.append(
                f"| {name} | {diagnostics['classification']} | "
                f"{diagnostics['generated_line_break_char_count']} | "
                f"{diagnostics['generated_crlf_pairs']} | "
                f"{diagnostics['generated_other_count']} | {exact} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Interpretation",
            "",
            "The harness distinguishes generated-PDF semantics from consumer behavior. A pass in one reader and failure in another for the same file is evidence of reader-specific extraction/copy reconstruction rather than an absent `/ToUnicode` mapping.",
            "",
            "Browser UI copy/paste and Acrobat/Preview are tracked separately in the manual checklist because their clipboard behavior is not exposed through a stable headless API in this harness.",
            "",
        ]
    )
    return "\n".join(lines)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    os.chdir(ROOT)
    cases = load_json(args.cases)["cases"]
    if args.selected_cases:
        selected = set(args.selected_cases)
        cases = [case for case in cases if case["name"] in selected]
        missing = selected - {case["name"] for case in cases}
        if missing:
            raise RuntimeError(f"unknown cases: {', '.join(sorted(missing))}")

    baseline_payload = load_json(args.baseline) if args.baseline.exists() else {}
    baseline = baseline_payload.get("expectations", {})
    diagnostic_baseline = baseline_payload.get("diagnostic_expectations", {})
    pdfjs_module = resolve_pdfjs_module(args.pdfjs_dist)
    availability = reader_availability(pdfjs_module)
    readers = choose_readers(args.readers, availability)
    required = {part.strip() for part in args.require.split(",") if part.strip()}
    for reader in required:
        if reader not in availability:
            raise RuntimeError(f"unknown required reader {reader!r}")
        if not availability[reader][0]:
            raise RuntimeError(f"required reader {reader!r} unavailable: {availability[reader][1]}")

    args.out.mkdir(parents=True, exist_ok=True)
    generated: dict[str, dict[str, Any]] = {}
    for case in cases:
        pdf = args.out / f"{case['name']}.pdf"
        metadata: dict[str, Any] = {"pdf": str(pdf), "fixture": case["fixture"]}
        if not args.no_generate:
            metadata.update(generate_pdf(case, pdf))
        elif not pdf.exists():
            raise RuntimeError(f"--no-generate requested but PDF is missing: {pdf}")
        generated[case["name"]] = metadata

    report: dict[str, Any] = {
        "schema": 1,
        "comparison": {
            "name": "selection_equivalent",
            "description": "CRLF/CR -> LF, remove form-feed page separators, ignore terminal LF only; preserve all other Unicode exactly",
        },
        "generated": generated,
        "readers": [],
        "regressions": [],
        "diagnostic_regressions": [],
        "improvements": [],
    }

    for reader in readers:
        is_available, reason = availability[reader]
        reader_report: dict[str, Any] = {
            "name": reader,
            "available": is_available,
            "availability": reason,
            "cases": [],
        }
        if not is_available:
            report["readers"].append(reader_report)
            continue

        extractor = extractor_for(reader, pdfjs_module)
        version: str | None = None
        for case in cases:
            name = case["name"]
            expected = (ROOT / case["fixture"]).read_text(encoding="utf-8")
            actual, detected_version = extractor(args.out / f"{name}.pdf")
            version = version or detected_version
            extracted_path = args.out / "extracted" / reader / f"{name}.txt"
            extracted_path.parent.mkdir(parents=True, exist_ok=True)
            extracted_path.write_text(actual, encoding="utf-8")
            comparison = compare_text(expected, actual)
            diagnostics: dict[str, Any] | None = None
            if reader == "pdfium":
                diagnostics = inspect_pdfium_generated_characters(args.out / f"{name}.pdf", expected)

            expectation = baseline.get(reader, {}).get(name, "untracked")
            diagnostic_expectation = diagnostic_baseline.get(reader, {}).get(name)
            case_report = {
                "name": name,
                "baseline": expectation,
                "extracted": str(extracted_path),
                "comparison": comparison,
            }
            if diagnostics is not None:
                case_report["reader_diagnostics"] = diagnostics
            report["diagnostic_regressions"].extend(
                check_diagnostic_expectation(
                    reader, name, diagnostics, diagnostic_expectation
                )
            )
            reader_report["cases"].append(case_report)

            passed = comparison["selection_exact"]
            if expectation == "pass" and not passed:
                report["regressions"].append({"reader": reader, "case": name})
            elif expectation == "known-fail" and passed:
                report["improvements"].append({"reader": reader, "case": name})
        reader_report["version"] = version or "unknown"
        report["readers"].append(reader_report)

    json_path = args.json_path or args.out / "results.json"
    markdown_path = args.markdown_path or args.out / "RESULTS.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")

    for reader in report["readers"]:
        if not reader["available"]:
            print(f"{reader['name']:8} SKIP ({reader['availability']})")
            continue
        statuses = " ".join(
            f"{case['name']}={'PASS' if case['comparison']['selection_exact'] else 'FAIL'}"
            for case in reader["cases"]
        )
        print(f"{reader['name']:8} {reader['version']}: {statuses}")

    print(f"json: {json_path}")
    print(f"markdown: {markdown_path}")
    if report["improvements"]:
        print("improvements:", ", ".join(f"{x['reader']}/{x['case']}" for x in report["improvements"]))
    if report["regressions"]:
        print("regressions:", ", ".join(f"{x['reader']}/{x['case']}" for x in report["regressions"]))
    if report["diagnostic_regressions"]:
        print(
            "diagnostic regressions:",
            ", ".join(
                f"{x['reader']}/{x['case']}:{x['field']}"
                for x in report["diagnostic_regressions"]
            ),
        )
    if args.check_baseline and (report["regressions"] or report["diagnostic_regressions"]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
