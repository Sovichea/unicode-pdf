"""Cargo command selection for conformance fixture generation."""

from __future__ import annotations

import os

SYSTEM_FEATURES = "system-harfbuzz,system-fribidi"


def cargo_cli_prefix() -> list[str]:
    """Return the Cargo prefix used to invoke ``unicode-pdf-cli``.

    Cross-reader conformance can pin the native HarfBuzz/FriBidi reference
    backend through ``UNICODE_PDF_CONFORMANCE_BACKEND=system``. The default
    remains the crate's normal Cargo feature set, which uses HarfRust and
    ``unicode-bidi``.
    """

    backend = os.environ.get("UNICODE_PDF_CONFORMANCE_BACKEND", "default")
    command = ["cargo", "run", "-q", "-p", "unicode-pdf-cli"]
    if backend == "default":
        pass
    elif backend == "system":
        command.extend(
            [
                "--no-default-features",
                "--features",
                SYSTEM_FEATURES,
            ]
        )
    else:
        raise RuntimeError(
            "UNICODE_PDF_CONFORMANCE_BACKEND must be 'default' or 'system', "
            f"got {backend!r}"
        )
    command.append("--")
    return command
