"""CLI-wide constants — kept in their own module to break import cycles.

Both ``workflows.py`` (Mode B subcommands) and ``primitives.py`` (Mode A
subcommands) need these. Putting them here means neither has to import
the other or ``main.py``.
"""

from __future__ import annotations

__version__ = "0.1.1"  # v1.4 — autofix agents + /fix-loop (issue #23)

DEFAULT_BACKEND = "codex"

# Pipe-friendly exit codes (used by every subcommand).
EXIT_OK = 0           # success; for verify-* this means decision=accepted
EXIT_REVIEW_FAIL = 1  # verify-* completed but verdict != accepted
EXIT_USAGE = 2        # bad CLI args / project not a blueprint
EXIT_RUNTIME = 3      # backend / parse / adapter failure
