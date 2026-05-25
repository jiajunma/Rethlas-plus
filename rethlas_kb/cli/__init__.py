"""rethlas-kb CLI package.

After issue #19 (pivot to Mode A), the CLI splits into:

- ``main`` — argparse dispatcher + ``main()`` entry point
- ``workflows`` — Mode B subcommands (Python end-to-end orchestration)
- ``primitives`` — Mode A subcommands (KbAdapter operations as tools)
- ``_io`` — shared helpers (project resolution, stdin, csv parsing)
- ``_constants`` — version, exit codes, default backend

External callers should import from this package, not the submodules
directly. The names re-exported below are the stable public surface.
"""

from __future__ import annotations

from ._constants import (
    DEFAULT_BACKEND,
    EXIT_OK,
    EXIT_REVIEW_FAIL,
    EXIT_RUNTIME,
    EXIT_USAGE,
    __version__,
)
from .main import _build_parser, _help_text, _register_backends, main

__all__ = [
    "DEFAULT_BACKEND",
    "EXIT_OK",
    "EXIT_REVIEW_FAIL",
    "EXIT_RUNTIME",
    "EXIT_USAGE",
    "__version__",
    "main",
]
