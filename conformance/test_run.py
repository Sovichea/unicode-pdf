import unittest

from run import compare_text, normalize_selection_text


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


if __name__ == "__main__":
    unittest.main()
