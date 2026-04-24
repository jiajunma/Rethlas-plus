"""Temporary workspace fixture (ARCHITECTURE §2.2 layout)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


_WORKSPACE_DIRS = (
    "events",
    "knowledge_base",
    "knowledge_base/nodes",
    "runtime/jobs",
    "runtime/logs",
    "runtime/locks",
    "runtime/state",
)

_ANNOTATED_TEMPLATE = """\
# Rethlas workspace configuration — all values default to those shown below
# if the file, a section, or a field is missing. Edit and restart
# `rethlas supervise` to take effect (no hot-reload).

[scheduling]
# §10.1 — target pass_count for each node before it counts as verified.
desired_pass_count             = 3
# §10.3 — max in-flight generator jobs per workspace.
generator_workers              = 2
# §10.3 — max in-flight verifier jobs per workspace.
verifier_workers               = 4
# §7.4 — kill threshold for a Codex subprocess whose log mtime goes stale.
codex_silent_timeout_seconds   = 1800

[dashboard]
# §6.7.1 — host:port the read-only dashboard binds to.
bind                           = "127.0.0.1:8765"
"""


def make_workspace(root: Path, *, seed_config: bool = False) -> Path:
    """Create the workspace skeleton under ``root``.

    Returns ``root`` for chainability.
    """
    root.mkdir(parents=True, exist_ok=True)
    for rel in _WORKSPACE_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    if seed_config:
        (root / "rethlas.toml").write_text(_ANNOTATED_TEMPLATE, encoding="utf-8")
    return root


@contextmanager
def tmp_workspace(
    tmp_path_factory_dir: Optional[Path] = None,
    *,
    seed_config: bool = False,
) -> Iterator[Path]:
    """Context-manager form: yields a workspace path.

    When running under pytest, prefer the ``tmp_path`` fixture and pass
    it as ``tmp_path_factory_dir``; callers outside pytest can rely on
    :mod:`tempfile` under the hood via a pytest-provided path.
    """
    if tmp_path_factory_dir is None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            yield make_workspace(Path(td), seed_config=seed_config)
    else:
        yield make_workspace(tmp_path_factory_dir, seed_config=seed_config)


__all__ = ["make_workspace", "tmp_workspace"]
