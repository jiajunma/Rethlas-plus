"""Startup cleanup for the runtime substrate.

When ``rethlas supervise`` boots, the previous run may have left behind:

- ``runtime/jobs/*.json`` for jobs whose owning processes are gone
- ``runtime/state/coordinator.json`` from a stopped coordinator
- ``runtime/state/librarian.json`` from a stopped librarian
- ``runtime/state/dashboard.json`` from a stopped dashboard child
- ``runtime/locks/*.lock`` files (lock content is fine to keep — flock
  state is per-fd, not per-file).

These are *observability* artefacts (§6.7.1); they do not carry truth.
We discard them so the new run starts from a clean canvas.

We MUST preserve:

- ``runtime/state/rejected_writes.jsonl``
- ``runtime/state/drift_alerts.jsonl``
- ``runtime/logs/*.log`` (Python daemon logs and Codex per-job logs)

Those are append-only operator history — losing them would make
incident triage impossible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from cli.workspace import WorkspacePaths


_PRESERVED_STATE_FILES = {
    "rejected_writes.jsonl",
    "drift_alerts.jsonl",
    "rebuild_in_progress.flag",
}


def cleanup_runtime(ws: WorkspacePaths) -> tuple[int, int]:
    """Remove stale job/state snapshots from a previous run.

    Returns ``(jobs_removed, snapshots_removed)`` for caller logging.
    """
    jobs_removed = _wipe_jobs(ws.runtime_jobs)
    snapshots_removed = _wipe_state_snapshots(ws.runtime_state)
    return jobs_removed, snapshots_removed


def _wipe_jobs(jobs_dir: Path) -> int:
    if not jobs_dir.is_dir():
        return 0
    count = 0
    for entry in jobs_dir.iterdir():
        if entry.is_file() and entry.suffix in {".json", ".tmp"}:
            try:
                entry.unlink()
                count += 1
            except FileNotFoundError:
                pass
    return count


def _wipe_state_snapshots(state_dir: Path) -> int:
    if not state_dir.is_dir():
        return 0
    count = 0
    for entry in state_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.name in _PRESERVED_STATE_FILES:
            continue
        # Only wipe known snapshot files. Anything else (operator notes,
        # custom JSON the user dropped in) is preserved.
        if entry.name in {"coordinator.json", "librarian.json", "dashboard.json"} or entry.suffix == ".tmp":
            try:
                entry.unlink()
                count += 1
            except FileNotFoundError:
                pass
    return count


__all__ = ["cleanup_runtime"]
