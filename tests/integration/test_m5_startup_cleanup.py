"""M5 — startup runtime cleanup preserves history, wipes stale snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.workspace import workspace_paths
from common.runtime.startup import cleanup_runtime
from tests.fixtures.tmp_workspace import make_workspace


def test_cleanup_wipes_jobs_and_state_snapshots(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    ws = workspace_paths(str(tmp_path))

    # Stale jobs.
    (ws.runtime_jobs / "ver-old.json").write_text('{"schema":"x"}', encoding="utf-8")
    (ws.runtime_jobs / "ver-old.json.tmp").write_text("partial", encoding="utf-8")
    # Stale snapshots.
    (ws.runtime_state / "coordinator.json").write_text('{"x":1}', encoding="utf-8")
    (ws.runtime_state / "librarian.json").write_text('{"x":1}', encoding="utf-8")
    # Append-only history we MUST preserve.
    (ws.runtime_state / "rejected_writes.jsonl").write_text(
        '{"reason":"x"}\n', encoding="utf-8"
    )
    (ws.runtime_state / "drift_alerts.jsonl").write_text(
        '{"reason":"y"}\n', encoding="utf-8"
    )
    # Daemon log we MUST preserve.
    (ws.runtime_logs / "supervise.log").write_text("history\n", encoding="utf-8")

    jobs_removed, snapshots_removed = cleanup_runtime(ws)
    assert jobs_removed == 2
    assert snapshots_removed == 2

    assert not (ws.runtime_jobs / "ver-old.json").exists()
    assert not (ws.runtime_jobs / "ver-old.json.tmp").exists()
    assert not (ws.runtime_state / "coordinator.json").exists()
    assert not (ws.runtime_state / "librarian.json").exists()

    assert (ws.runtime_state / "rejected_writes.jsonl").exists()
    assert (ws.runtime_state / "drift_alerts.jsonl").exists()
    assert (ws.runtime_logs / "supervise.log").exists()


def test_cleanup_idempotent(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    ws = workspace_paths(str(tmp_path))
    cleanup_runtime(ws)
    # Second call must be a no-op.
    jobs, snaps = cleanup_runtime(ws)
    assert jobs == 0
    assert snaps == 0


def test_cleanup_preserves_rebuild_flag(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    ws = workspace_paths(str(tmp_path))
    flag = ws.runtime_state / "rebuild_in_progress.flag"
    flag.write_text('{"started_at":"x"}', encoding="utf-8")
    cleanup_runtime(ws)
    assert flag.exists(), "rebuild flag must survive runtime cleanup"
