"""`rethlas rebuild` — project ``events/`` into a fresh ``knowledge_base/``.

Contract (ARCHITECTURE §6.5 / §11.2):
- Refuse if ``runtime/locks/supervise.lock`` is held — concurrent
  projection would deadlock librarian. Operator must stop supervise
  first; we do not auto-kill it.
- Take the lock while running. A crash mid-rebuild leaves
  ``runtime/state/rebuild_in_progress.flag`` on disk; librarian's
  next startup (M4) detects the flag and forces a clean rebuild
  before accepting new work.
- **Never** mutate ``events/`` — only the projection is rebuilt.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli.workspace import WorkspacePaths, ensure_initialised, workspace_paths
from common.kb.kuzu_backend import KuzuBackend
from librarian.rebuild import rebuild_from_events


def _utc_now_ms_iso() -> str:
    now = datetime.now(tz=timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _try_acquire_lock(ws: WorkspacePaths):
    ws.runtime_locks.mkdir(parents=True, exist_ok=True)
    lock_path = ws.supervise_lock
    # Create the file if absent.
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return None
        raise
    return fd


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_flag(ws: WorkspacePaths) -> None:
    ws.runtime_state.mkdir(parents=True, exist_ok=True)
    ws.rebuild_flag.write_text(
        json.dumps(
            {"started_at": _utc_now_ms_iso()}, sort_keys=True, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )


def _remove_flag(ws: WorkspacePaths) -> None:
    try:
        ws.rebuild_flag.unlink()
    except FileNotFoundError:
        pass


def run_rebuild(workspace: str | None) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)

    fd = _try_acquire_lock(ws)
    if fd is None:
        sys.stderr.write(
            "rethlas supervise is running — stop it before running "
            "`rethlas rebuild`.\n"
        )
        return 1

    try:
        _write_flag(ws)
        # Wipe ONLY the knowledge_base projection; events/ stays intact.
        kb_dir = ws.knowledge_base
        for entry in kb_dir.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        ws.nodes_dir.mkdir(parents=True, exist_ok=True)

        backend = KuzuBackend(ws.dag_kz)
        try:
            trail = rebuild_from_events(backend=backend, events_root=ws.events)
        finally:
            backend.close()

        applied_count = sum(1 for _, status, _ in trail if status == "applied")
        failed_count = sum(1 for _, status, _ in trail if status == "apply_failed")
        sys.stdout.write(
            f"rebuild complete: {applied_count} applied, {failed_count} apply_failed\n"
        )
        _remove_flag(ws)
        return 0
    finally:
        _release_lock(fd)
