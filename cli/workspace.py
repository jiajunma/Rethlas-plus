"""Workspace path resolution + annotated rethlas.toml template (§2.2 / §2.4)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Fields in the annotated template MUST match ARCHITECTURE §2.4 exactly.
ANNOTATED_RETHLAS_TOML: Final[str] = """\
# Rethlas workspace configuration (ARCHITECTURE §2.4).
# Every field shown below is OPTIONAL — missing values fall back to the
# documented default. The file is read once at process startup; edit and
# restart `rethlas supervise` to take effect (no hot-reload).

[scheduling]
# §10.1 — target pass_count before a node counts as verified.
desired_pass_count             = 3
# §10.3 — max concurrent generator jobs per workspace.
generator_workers              = 2
# §10.3 — max concurrent verifier jobs per workspace.
verifier_workers               = 4
# §7.4 — kill threshold (seconds) for a Codex subprocess whose
# log mtime has gone stale.
codex_silent_timeout_seconds   = 1800

[dashboard]
# §6.7.1 — host:port for the read-only dashboard HTTP server.
bind                           = "127.0.0.1:8765"
"""


# Files / directories created by `rethlas init`.
_WORKSPACE_DIRS: Final[tuple[str, ...]] = (
    "events",
    "knowledge_base",
    "knowledge_base/nodes",
    "runtime/jobs",
    "runtime/logs",
    "runtime/locks",
    "runtime/state",
)

_WORKSPACE_GITIGNORE: Final[str] = """\
# Rethlas — derived / runtime state.
/knowledge_base/dag.kz/
/knowledge_base/nodes/
/runtime/
"""


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Canonical paths inside a Rethlas workspace."""

    root: Path

    @property
    def rethlas_toml(self) -> Path:
        return self.root / "rethlas.toml"

    @property
    def events(self) -> Path:
        return self.root / "events"

    @property
    def knowledge_base(self) -> Path:
        return self.root / "knowledge_base"

    @property
    def dag_kz(self) -> Path:
        return self.knowledge_base / "dag.kz"

    @property
    def nodes_dir(self) -> Path:
        return self.knowledge_base / "nodes"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def runtime_jobs(self) -> Path:
        return self.runtime / "jobs"

    @property
    def runtime_logs(self) -> Path:
        return self.runtime / "logs"

    @property
    def runtime_locks(self) -> Path:
        return self.runtime / "locks"

    @property
    def runtime_state(self) -> Path:
        return self.runtime / "state"

    @property
    def supervise_lock(self) -> Path:
        return self.runtime_locks / "supervise.lock"

    @property
    def rebuild_flag(self) -> Path:
        return self.runtime_state / "rebuild_in_progress.flag"

    @property
    def rejected_writes_jsonl(self) -> Path:
        return self.runtime_state / "rejected_writes.jsonl"


def resolve_workspace_root(explicit: str | None) -> Path:
    """Return the workspace root, honouring ``--workspace`` then CWD."""
    if explicit is not None:
        return Path(explicit).resolve()
    return Path.cwd().resolve()


def workspace_paths(explicit: str | None) -> WorkspacePaths:
    return WorkspacePaths(root=resolve_workspace_root(explicit))


def is_initialised(ws: WorkspacePaths) -> bool:
    """A workspace counts as initialised when ``events/`` AND ``rethlas.toml`` exist."""
    return ws.events.is_dir() and ws.rethlas_toml.is_file()


def ensure_initialised(ws: WorkspacePaths) -> None:
    """Exit with the documented "not initialised" message + code 2 if missing.

    Callers raise :class:`SystemExit` which ``cli/main.py``'s argparse
    wrapper surfaces as the process exit code.
    """
    if not is_initialised(ws):
        import sys

        sys.stderr.write(
            f"workspace not initialized at {ws.root}; run `rethlas init` first\n"
        )
        raise SystemExit(2)


def create_workspace_layout(ws: WorkspacePaths, *, annotated_template: bool = True) -> None:
    """Build the §2.2 directory skeleton and write the default config + .gitignore.

    Callers of ``rethlas init`` run this exactly once. It is idempotent
    for directories but does NOT overwrite an existing ``rethlas.toml`` —
    that decision belongs to ``--force`` handling.
    """
    ws.root.mkdir(parents=True, exist_ok=True)
    for rel in _WORKSPACE_DIRS:
        (ws.root / rel).mkdir(parents=True, exist_ok=True)

    if annotated_template and not ws.rethlas_toml.exists():
        ws.rethlas_toml.write_text(ANNOTATED_RETHLAS_TOML, encoding="utf-8")

    gi = ws.root / ".gitignore"
    if not gi.exists():
        gi.write_text(_WORKSPACE_GITIGNORE, encoding="utf-8")


__all__ = [
    "ANNOTATED_RETHLAS_TOML",
    "WorkspacePaths",
    "create_workspace_layout",
    "ensure_initialised",
    "is_initialised",
    "resolve_workspace_root",
    "workspace_paths",
]
