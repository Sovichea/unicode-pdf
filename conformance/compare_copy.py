#!/usr/bin/env python3
"""Compare manually pasted UTF-8 text with a conformance fixture."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from run import compare_text, normalize_selection_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare clipboard/pasted UTF-8 against an expected fixture by Unicode scalar sequence."
    )
    parser.add_argument("expected", type=Path)
    parser.add_argument("pasted", type=Path)
    args = parser.parse_args()

    expected = args.expected.read_text(encoding="utf-8")
    actual = args.pasted.read_text(encoding="utf-8")
    comparison = compare_text(expected, actual)

    print(f"raw_exact: {comparison['raw_exact']}")
    print(f"selection_exact: {comparison['selection_exact']}")
    print(f"nfc_exact: {comparison['nfc_exact']}")
    print(f"similarity: {comparison['similarity']:.6f}")
    print(f"expected_scalars: {comparison['expected_scalars']}")
    print(f"actual_scalars: {comparison['actual_scalars']}")

    if comparison["selection_exact"]:
        return 0

    mismatch = comparison["first_mismatch"]
    if mismatch:
        print(f"first_mismatch: scalar {mismatch['index']}")
        print("expected context:")
        for item in mismatch["expected"]:
            print(f"  {item['index']:>5} {item['codepoint']} {item['name']} {item['char']!r}")
        print("actual context:")
        for item in mismatch["actual"]:
            print(f"  {item['index']:>5} {item['codepoint']} {item['name']} {item['char']!r}")

    print("actual selection-normalized text:")
    print(normalize_selection_text(actual))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
