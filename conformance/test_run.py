import subprocess
import sys
import unittest
from pathlib import Path

from run import analyze_generated_character_pages, compare_text, normalize_selection_text


class ReaderAvailabilityImportTests(unittest.TestCase):
    def test_importlib_util_is_available_in_clean_python_process(self):
        conformance_dir = Path(__file__).resolve().parent
        script = (
            "import importlib, sys; "
            "assert not hasattr(importlib, 'util'); "
            f"sys.path.insert(0, {str(conformance_dir)!r}); "
            "import run; "
            "run.reader_availability(None)"
        )
        subprocess.run([sys.executable, "-S", "-c", script], check=True)


class SelectionNormalizationTests(unittest.TestCase):
    def test_normalizes_only_transport_line_endings(self):
        self.assertEqual(normalize_selection_text("a\r\nb\r\f\n"), "a\nb")

    def test_preserves_bidi_controls(self):
        text = "\u202bالعربية\u202c"
        self.assertEqual(normalize_selection_text(text), text)

    def test_preserves_combining_mark_order(self):
        text = "a\u0301\u0323"
        self.assertEqual(normalize_selection_text(text), text)

    def test_preserves_internal_whitespace(self):
        self.assertEqual(normalize_selection_text("a  b\n"), "a  b")


class ComparisonTests(unittest.TestCase):
    def test_exact_after_terminal_newline_transport(self):
        result = compare_text("abc\n", "abc\r\n\f")
        self.assertTrue(result["selection_exact"])

    def test_does_not_accept_canonical_normalization_as_exact(self):
        result = compare_text("é", "e\u0301")
        self.assertFalse(result["selection_exact"])
        self.assertTrue(result["nfc_exact"])

    def test_does_not_repair_bidi_reordering(self):
        result = compare_text("abc العربية 123", "abc 123 العربية")
        self.assertFalse(result["selection_exact"])


class PdfiumGeneratedCharacterTests(unittest.TestCase):
    def test_classifies_generated_crlf_as_reader_linebreaks_only(self):
        pages = [[
            ("a", False),
            ("b", False),
            ("\r", True),
            ("\n", True),
            ("c", False),
            ("d", False),
        ]]
        result = analyze_generated_character_pages("abcd", pages)
        self.assertEqual(result["classification"], "reader-generated-line-breaks-only")
        self.assertEqual(result["generated_line_break_char_count"], 2)
        self.assertEqual(result["generated_crlf_pairs"], 1)
        self.assertEqual(result["generated_other_count"], 0)
        self.assertTrue(result["exact_after_removing_generated_linebreaks"])

    def test_generated_non_linebreak_is_not_classified_as_linebreak_only(self):
        pages = [[("a", False), (" ", True), ("b", False)]]
        result = analyze_generated_character_pages("ab", pages)
        self.assertEqual(
            result["classification"],
            "reader-generated-characters-plus-other-differences",
        )
        self.assertEqual(result["generated_other_count"], 1)
        self.assertFalse(result["exact_after_removing_generated_linebreaks"])

    def test_real_newline_is_never_removed_by_generated_character_diagnostic(self):
        pages = [[("a", False), ("\n", False), ("b", False)]]
        result = analyze_generated_character_pages("ab", pages)
        self.assertEqual(result["classification"], "no-reader-generated-characters")
        self.assertFalse(result["exact_after_removing_generated_linebreaks"])


if __name__ == "__main__":
    unittest.main()
