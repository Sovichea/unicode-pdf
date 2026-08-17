import unittest

from check_paragraph_semantics import decode_actual_text, source_paragraphs


class ParagraphSemanticTests(unittest.TestCase):
    def test_source_paragraphs_preserves_only_hard_breaks(self):
        self.assertEqual(source_paragraphs("abc\ndef"), ["abc", "def"])
        self.assertEqual(source_paragraphs("ខ្មែរ"), ["ខ្មែរ"])

    def test_decode_actual_text(self):
        self.assertEqual(decode_actual_text("178117D2179817C2179A".encode()), "ខ្មែរ")


if __name__ == "__main__":
    unittest.main()
