"""Shared CLI helpers — blueprint resolution, stdin handling, CSV parsing.

Kept independent of every subcommand module so any of them can import
from here without forming a cycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rethlas_kb.adapter import KbAdapter


def resolve_blueprint(blueprint_arg: str) -> Path:
    """Expand ``~`` and resolve to absolute; don't check existence."""
    return Path(blueprint_arg).expanduser().resolve()


def adapter_for(blueprint_arg: str) -> tuple[KbAdapter | None, str | None]:
    """Build a ``KbAdapter`` from a ``--blueprint`` argument.

    Returns ``(adapter, None)`` on success or ``(None, error_message)``
    if the path doesn't look like a mdblueprint repo (no
    ``docs/knowledge/`` subdirectory).
    """
    blueprint = resolve_blueprint(blueprint_arg)
    if not (blueprint / "docs" / "knowledge").exists():
        return None, (
            f"--blueprint {blueprint} has no docs/knowledge/ directory "
            f"— is this a mdblueprint repo?"
        )
    return KbAdapter(blueprint), None


def read_stdin_text() -> str:
    """Read all of stdin as text (used by ``--raw -`` and ``--from-file -``)."""
    return sys.stdin.read()


def read_file_or_stdin(path_or_dash: str) -> str:
    """Read text from a file path, or stdin if ``path_or_dash == '-'``."""
    if path_or_dash == "-":
        return read_stdin_text()
    return Path(path_or_dash).expanduser().read_text(encoding="utf-8")


def split_csv(value: str | None) -> list[str]:
    """Parse ``"a,b,c"`` into ``["a", "b", "c"]`` (empties skipped)."""
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Legacy alias — kept for one release so external callers (if any) don't break.
# ---------------------------------------------------------------------------
def resolve_project(project_arg: str) -> Path:
    """Deprecated: use :func:`resolve_blueprint`."""
    return resolve_blueprint(project_arg)
