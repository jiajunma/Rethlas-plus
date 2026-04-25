"""M7 — ``rethlas verifier --target X`` system test."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from librarian.heartbeat import PHASE_READY
from tests.fixtures.librarian_proc import librarian
from tests.fixtures.scripted_codex import fake_codex_argv, quick_success


PYTHON = sys.executable


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )


def _setup_workspace(tmp_path: Path) -> None:
    r = _run("--workspace", str(tmp_path), "init")
    assert r.returncode == 0, r.stderr
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "lem:simple",
        "--kind", "lemma",
        "--statement", "A simple lemma",
        "--proof", "trivial.",
        "--actor", "user:alice",
    )
    assert r.returncode == 0, r.stderr
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)


def _accepted_verdict(vh: str = "sha256:" + "x" * 64) -> str:
    return json.dumps({
        "verdict": "accepted",
        "verification_hash": vh,
        "verification_report": {
            "summary": "ok",
            "checked_items": [],
            "gaps": [],
            "critical_errors": [],
            "external_reference_checks": [],
        },
        "repair_hint": "",
    })


def test_verifier_publishes_event(tmp_path: Path) -> None:
    _setup_workspace(tmp_path)

    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = quick_success(_accepted_verdict())

    r = _run(
        "--workspace", str(tmp_path),
        "verifier",
        "--target", "lem:simple",
        "--codex-argv", " ".join(fake_codex_argv()),
        "--silent-timeout-s", "5.0",
        "--actor", "verifier:test",
        env=env,
    )
    assert r.returncode == 0, r.stderr
    events = [
        p for p in (tmp_path / "events").rglob("*.json")
        if "verifier.run_completed" in p.name
    ]
    assert len(events) == 1
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "verifier.run_completed"
    assert body["target"] == "lem:simple"
    payload = body["payload"]
    assert payload["verdict"] == "accepted"


def test_verifier_unknown_target_exits_2(tmp_path: Path) -> None:
    _setup_workspace(tmp_path)
    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = quick_success(_accepted_verdict())
    r = _run(
        "--workspace", str(tmp_path),
        "verifier",
        "--target", "lem:absent",
        "--codex-argv", " ".join(fake_codex_argv()),
        env=env,
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "absent" in r.stderr.lower()
