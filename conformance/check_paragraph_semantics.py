#!/usr/bin/env python3
"""Validate compiler-authored logical paragraph semantics in generated PDFs.

This checks the PDF structure emitted by unicode-pdf itself, not a reader's
geometry heuristics. Paragraph /ActualText must equal the pre-layout source
paragraph exactly, while visual soft wraps/pages remain absent from that text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "conformance" / "cases.json"

PARAGRAPH_RE = re.compile(
    rb"/Type\s*/StructElem\s*/S\s*/P\b(?:(?!endobj).)*?/ActualText\s*<FEFF([0-9A-Fa-f]+)>",
    re.DOTALL,
)


def source_paragraphs(text: str) -> list[str]:
    if text == "":
        return [""]
    paragraphs: list[str] = []
    for part in text.splitlines(keepends=True):
        paragraphs.append(part[:-1] if part.endswith("\n") else part)
    if not paragraphs:
        paragraphs.append(text)
    return paragraphs


def decode_actual_text(hex_bytes: bytes) -> str:
    data = bytes.fromhex(hex_bytes.decode("ascii"))
    return data.decode("utf-16-be")


def inspect_pdf(pdf: Path) -> tuple[bool, list[str]]:
    raw = pdf.read_bytes()
    has_document = b"/Type /StructElem /S /Document" in raw
    paragraphs = [decode_actual_text(match) for match in PARAGRAPH_RE.findall(raw)]
    return has_document, paragraphs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=ROOT / "target" / "conformance")
    parser.add_argument(
        "--case",
        action="append",
        default=["khmer-paragraph-natural", "khmer-paragraph-multipage"],
    )
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = {case["name"]: case for case in payload["cases"]}
    results = []
    failed = False

    for name in args.case:
        case = cases.get(name)
        if case is None:
            raise SystemExit(f"unknown case: {name}")
        source = (ROOT / case["fixture"]).read_text(encoding="utf-8")
        expected = source_paragraphs(source)
        pdf = args.out / f"{name}.pdf"
        if not pdf.is_file():
            raise SystemExit(f"missing generated PDF: {pdf}")
        has_document, actual = inspect_pdf(pdf)
        exact = actual == expected
        passed = has_document and exact
        failed |= not passed
        results.append(
            {
                "case": name,
                "document_tag": has_document,
                "paragraph_count": len(actual),
                "expected_paragraph_count": len(expected),
                "paragraph_text_exact": exact,
                "passed": passed,
            }
        )

    report = {"schema": 1, "results": results}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output, encoding="utf-8")
    print(output, end="")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
