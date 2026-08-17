import os
import unittest
from unittest.mock import patch

from cargo_backend import SYSTEM_FEATURES, cargo_cli_prefix


class CargoBackendTests(unittest.TestCase):
    def test_default_backend_uses_normal_features(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                cargo_cli_prefix(),
                ["cargo", "run", "-q", "-p", "unicode-pdf-cli", "--"],
            )

    def test_system_backend_disables_defaults_and_enables_native_features(self):
        with patch.dict(
            os.environ,
            {"UNICODE_PDF_CONFORMANCE_BACKEND": "system"},
            clear=True,
        ):
            self.assertEqual(
                cargo_cli_prefix(),
                [
                    "cargo",
                    "run",
                    "-q",
                    "-p",
                    "unicode-pdf-cli",
                    "--no-default-features",
                    "--features",
                    SYSTEM_FEATURES,
                    "--",
                ],
            )

    def test_invalid_backend_is_rejected(self):
        with patch.dict(
            os.environ,
            {"UNICODE_PDF_CONFORMANCE_BACKEND": "invalid"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "default.*system"):
                cargo_cli_prefix()


if __name__ == "__main__":
    unittest.main()
