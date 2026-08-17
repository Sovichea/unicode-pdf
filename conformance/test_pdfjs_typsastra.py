from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_pdfjs_typsastra", ROOT / "conformance/run_pdfjs_typsastra.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

PATCH_SPEC = importlib.util.spec_from_file_location(
    "apply_typsastra_patch", ROOT / "integrations/pdfjs/apply_typsastra_patch.py"
)
assert PATCH_SPEC and PATCH_SPEC.loader
PATCH = importlib.util.module_from_spec(PATCH_SPEC)
PATCH_SPEC.loader.exec_module(PATCH)


class PdfJsTypsastraTests(unittest.TestCase):
    def test_source_byte_range_uses_utf8_offsets(self):
        source = "A កម្ពុជា B"
        start, end = MODULE.source_byte_range(source, "កម្ពុជា")
        self.assertEqual(source.encode("utf-8")[start:end].decode("utf-8"), "កម្ពុជា")

    def test_full_overlap_is_one(self):
        expected = [10.0, 20.0, 30.0, 40.0]
        self.assertEqual(MODULE.intersection_coverage(expected, expected), 1.0)

    def test_missing_overlap_is_zero(self):
        self.assertEqual(
            MODULE.intersection_coverage([0.0, 0.0, 10.0, 10.0], [20.0, 20.0, 30.0, 30.0]),
            0.0,
        )

    def test_detects_supported_pdfjs_version(self):
        self.assertEqual(PATCH.detect_version("/* pdfjsVersion = 6.2.108 */"), "6.2.108")

    def test_patch_main_exposes_logical_mode(self):
        source = """  streamTextContent({
    includeMarkedContent = false,
    disableNormalization = false
  } = {}) {
      includeMarkedContent: includeMarkedContent === true,
      disableNormalization: disableNormalization === true
    }, {
    textDiv.textContent = geom.str;
    textDiv.dir = geom.dir;
    if (geom.str.length > 1) {
      shouldScaleText = true;
"""
        patched = PATCH.patch_main(source)
        self.assertIn("preserveLogicalText = false", patched)
        self.assertIn("preserveLogicalText: preserveLogicalText === true", patched)
        self.assertIn("geom.preserveLogicalText || geom.str.length > 1", patched)

    def test_patch_viewer_preserves_logical_clipboard(self):
        source = """      const selection = document.getSelection();
        event.clipboardData.setData("text/plain", removeNullCharacters(normalizeUnicode(selection.toString())));"""
        patched = PATCH.patch_viewer(source)
        self.assertIn("selectedText = selection.toString()", patched)
        self.assertIn("dataset.preserveLogicalText", patched)
        self.assertIn("? selectedText : normalizeUnicode(selectedText)", patched)


if __name__ == "__main__":
    unittest.main()
