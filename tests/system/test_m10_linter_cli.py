"""M10 — `rethlas linter` CLI exit codes and report path.

§2.3 exit-code table:
- 0  → no violations
- 5  → violations found
- 2  → supervise lock held without --allow-concurrent
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


PYTHON = sys.executable


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, check=False, cwd=cwd,
    )


def test_exit_code_5_when_violations(tmp_path: Path) -> None:
    r = _run("--workspace", str(tmp_path), "init")
    assert r.returncode == 0
    # Write an event file that does not match its filename's event_id.
    shard = tmp_path / "events" / "2026-04-25"
    shard.mkdir(parents=True, exist_ok=True)
    body = {
        "event_id": "20260425T000000.000-0001-aaaaaaaaaaaaaaaa",
        "type": "user.node_added",
        "actor": "user:alice",
        "ts": "2026-04-25T00:00:00.000Z",
        "payload": {},
    }
    bad_filename = shard / (
        "20260425T000000.000--user.node_added--none--user_alice--0001--bbbbbbbbbbbbbbbb.json"
    )
    bad_filename.write_text(json.dumps(body), encoding="utf-8")

    r = _run("--workspace", str(tmp_path), "linter")
    assert r.returncode == 5, r.stdout + r.stderr
    report = json.loads(
        (tmp_path / "runtime" / "state" / "linter_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert any(
        v["code"] == "A_event_id_mismatch" for v in report["a"]["violations"]
    )


def test_exit_code_0_on_clean_init(tmp_path: Path) -> None:
    r = _run("--workspace", str(tmp_path), "init")
    assert r.returncode == 0
    r = _run("--workspace", str(tmp_path), "linter")
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(
        (tmp_path / "runtime" / "state" / "linter_report.json").read_text(
            encoding="utf-8"
        )
    )
    for cat in ("a", "b", "c", "d", "e", "f"):
        assert report[cat]["count"] == 0
