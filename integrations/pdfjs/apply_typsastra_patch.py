#!/usr/bin/env python3
"""Patch pdfjs-dist 6.2.108 for compiler-authored logical PDF text.

The patch is intentionally narrow and opt-in. It adds a `preserveLogicalText`
parameter to PDFPageProxy.getTextContent/streamTextContent. When enabled,
PDF.js:

* preserves explicit PDF whitespace instead of reconstructing it geometrically;
* keeps compiler-provided logical Unicode order instead of applying a second
  BiDi reordering pass to the extracted string;
* treats a multi-codepoint `/ToUnicode` value as a logical PDF character rather
  than a zero-width diacritic merely because it contains an Mn/Cf scalar;
* isolates multi-codepoint logical glyphs as text items so DOM selection has a
  stable PDF advance for each shaping unit; and
* scales every logical-mode TextLayer item to the PDF-provided width, including
  one-character items and explicit spaces.

This script targets the built `pdfjs-dist` package because Typsastra consumes
that package directly. It refuses unknown versions instead of silently applying
partial edits.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

SUPPORTED_VERSION = "6.2.108"


class PatchError(RuntimeError):
    """Raised when the expected PDF.js source does not match."""


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def detect_version(pdf_main: str) -> str:
    match = re.search(r"pdfjsVersion\s*=\s*([0-9.]+)", pdf_main)
    if not match:
        raise PatchError("could not determine pdfjsVersion from build/pdf.mjs")
    return match.group(1)


def patch_main(text: str) -> str:
    text = replace_once(
        text,
        """  streamTextContent({
    includeMarkedContent = false,
    disableNormalization = false
  } = {}) {""",
        """  streamTextContent({
    includeMarkedContent = false,
    disableNormalization = false,
    preserveLogicalText = false
  } = {}) {""",
        "streamTextContent signature",
    )
    text = replace_once(
        text,
        """      includeMarkedContent: includeMarkedContent === true,
      disableNormalization: disableNormalization === true
    }, {""",
        """      includeMarkedContent: includeMarkedContent === true,
      disableNormalization: disableNormalization === true,
      preserveLogicalText: preserveLogicalText === true
    }, {""",
        "GetTextContent message",
    )
    text = replace_once(
        text,
        """    textDiv.textContent = geom.str;
    textDiv.dir = geom.dir;""",
        """    textDiv.textContent = geom.str;
    textDiv.dir = geom.dir;
    if (geom.preserveLogicalText) {
      this.#rootContainer.dataset.preserveLogicalText = "true";
    }""",
        "TextLayer logical-mode marker",
    )
    text = replace_once(
        text,
        """    if (geom.str.length > 1) {
      shouldScaleText = true;""",
        """    if (geom.preserveLogicalText || geom.str.length > 1) {
      shouldScaleText = true;""",
        "TextLayer exact logical geometry",
    )
    return text


def patch_worker(text: str) -> str:
    text = replace_once(
        text,
        r"const SpecialCharRegExp = /^(\s)|(\p{Mn})|(\p{Cf})$/u;",
        r"const SpecialCharRegExp = /^(\s)$|^(\p{Mn})$|^(\p{Cf})$/u;",
        "Unicode category regexp",
    )
    text = replace_once(
        text,
        """    markedContentData = null,
    disableNormalization = false,
    keepWhiteSpace = false,
    prevRefs = null,
    intersector = null
  }) {
    if (stream.isAsync) {""",
        """    markedContentData = null,
    disableNormalization = false,
    keepWhiteSpace = false,
    preserveLogicalText = false,
    prevRefs = null,
    intersector = null
  }) {
    if (preserveLogicalText) {
      keepWhiteSpace = true;
    }
    if (stream.isAsync) {""",
        "PartialEvaluator.getTextContent signature",
    )
    text = replace_once(
        text,
        """      if (!disableNormalization) {
        text = normalizeUnicode(text);
      }""",
        """      if (!disableNormalization && !preserveLogicalText) {
        text = normalizeUnicode(text);
      }""",
        "logical text normalization",
    )
    text = replace_once(
        text,
        """        str: bidiResult.str,
        dir: bidiResult.dir,""",
        """        str: preserveLogicalText ? text : bidiResult.str,
        dir: bidiResult.dir,
        preserveLogicalText,""",
        "logical text BiDi handling",
    )
    text = replace_once(
        text,
        """        const textChunk = ensureTextContentItem();
        if (category.isZeroWidthDiacritic) {""",
        """        const isolateLogicalGlyph = preserveLogicalText && Array.from(glyph.unicode).length > 1;
        if (isolateLogicalGlyph) {
          flushTextContentItem();
        }
        const textChunk = ensureTextContentItem();
        if (category.isZeroWidthDiacritic) {""",
        "logical glyph isolation start",
    )
    text = replace_once(
        text,
        """        if (charSpacing) {
          if (!font.vertical) {
            textState.translateTextMatrix(charSpacing * textState.textHScale, 0);
          } else {
            textState.translateTextMatrix(0, -charSpacing);
          }
        }
      }
    }
    function appendEOL() {""",
        """        if (charSpacing) {
          if (!font.vertical) {
            textState.translateTextMatrix(charSpacing * textState.textHScale, 0);
          } else {
            textState.translateTextMatrix(0, -charSpacing);
          }
        }
        if (isolateLogicalGlyph) {
          flushTextContentItem();
        }
      }
    }
    function appendEOL() {""",
        "logical glyph isolation end",
    )
    text = replace_once(
        text,
        """                disableNormalization,
                keepWhiteSpace,
                prevRefs: seenRefs""",
        """                disableNormalization,
                keepWhiteSpace,
                preserveLogicalText,
                prevRefs: seenRefs""",
        "recursive text extraction",
    )
    text = replace_once(
        text,
        """  async extractTextContent({
    handler,
    task,
    includeMarkedContent,
    disableNormalization,
    sink,""",
        """  async extractTextContent({
    handler,
    task,
    includeMarkedContent,
    disableNormalization,
    preserveLogicalText = false,
    sink,""",
        "page extractTextContent signature",
    )
    text = replace_once(
        text,
        """      includeMarkedContent,
      disableNormalization,
      sink,
      viewBox: this.view,""",
        """      includeMarkedContent,
      disableNormalization,
      preserveLogicalText,
      sink,
      viewBox: this.view,""",
        "page to evaluator text options",
    )
    text = replace_once(
        text,
        """    handler.on("GetTextContent", function ({
      pageId,
      pageIndex,
      includeMarkedContent,
      disableNormalization
    }, sink) {""",
        """    handler.on("GetTextContent", function ({
      pageId,
      pageIndex,
      includeMarkedContent,
      disableNormalization,
      preserveLogicalText
    }, sink) {""",
        "worker GetTextContent handler",
    )
    text = replace_once(
        text,
        """          includeMarkedContent,
          disableNormalization
        }).then(() => {""",
        """          includeMarkedContent,
          disableNormalization,
          preserveLogicalText
        }).then(() => {""",
        "worker page extraction call",
    )
    return text


def patch_viewer(text: str) -> str:
    return replace_once(
        text,
        """      const selection = document.getSelection();
        event.clipboardData.setData("text/plain", removeNullCharacters(normalizeUnicode(selection.toString())));""",
        """      const selection = document.getSelection();
        const selectedText = selection.toString();
        event.clipboardData.setData(
          "text/plain",
          removeNullCharacters(this.div.dataset.preserveLogicalText === "true" ? selectedText : normalizeUnicode(selectedText))
        );""",
        "TextLayerBuilder logical-mode clipboard",
    )


def distribution_paths(source: Path) -> tuple[Path, Path, Path | None]:
    """Returns required PDF.js files and an optional full-viewer module."""
    main_source = source / "build" / "pdf.mjs"
    worker_source = source / "build" / "pdf.worker.mjs"
    viewer_source = source / "web" / "viewer.mjs"
    if not main_source.is_file() or not worker_source.is_file():
        raise PatchError("input does not look like a pdfjs-dist directory")
    return main_source, worker_source, viewer_source if viewer_source.is_file() else None


def apply_patch(source: Path, output: Path) -> str:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise PatchError("input and output directories must be different")
    main_source, worker_source, viewer_source = distribution_paths(source)

    main_text = main_source.read_text(encoding="utf-8")
    version = detect_version(main_text)
    if version != SUPPORTED_VERSION:
        raise PatchError(
            f"unsupported pdfjs-dist version {version}; expected {SUPPORTED_VERSION}"
        )

    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(source, output)

    output_main = output / "build" / "pdf.mjs"
    output_worker = output / "build" / "pdf.worker.mjs"
    output_main.write_text(patch_main(main_text), encoding="utf-8")
    output_worker.write_text(
        patch_worker(worker_source.read_text(encoding="utf-8")), encoding="utf-8"
    )
    if viewer_source is not None:
        output_viewer = output / "web" / "viewer.mjs"
        output_viewer.write_text(
            patch_viewer(viewer_source.read_text(encoding="utf-8")), encoding="utf-8"
        )
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="stock pdfjs-dist")
    parser.add_argument("--output", type=Path, required=True, help="patched copy")
    args = parser.parse_args()
    try:
        version = apply_patch(args.input, args.output)
    except PatchError as error:
        parser.error(str(error))
    print(f"patched pdfjs-dist {version}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
