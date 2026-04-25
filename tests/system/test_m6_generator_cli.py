"""M6 — ``rethlas generator --target X --mode fresh|repair`` system tests.

These tests exercise the full CLI flow: init workspace → add a node
that needs proving → run librarian once to populate Kuzu/nodes →
spawn ``rethlas generator`` with fake codex → verify a
``generator.batch_committed`` event landed under ``events/``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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


def _node_block(label: str, kind: str, statement: str, proof: str) -> str:
    return (
        f"<node>\n"
        f"label: {label}\n"
        f"kind: {kind}\n"
        f"---\n"
        f"**Statement.**\n\n{statement}\n\n**Proof.**\n\n{proof}\n"
        f"</node>"
    )


def _setup_workspace_with_target(tmp_path: Path) -> None:
    """Init workspace, add a theorem with empty proof, run librarian once."""
    r = _run("--workspace", str(tmp_path), "init")
    assert r.returncode == 0, r.stderr

    # Add the target with NO proof so it goes to generator queue.
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "thm:goal",
        "--kind", "theorem",
        "--statement", "Statement of the goal.",
        "--actor", "user:alice",
    )
    assert r.returncode == 0, r.stderr

    # Drive librarian through a startup so the event is applied to Kuzu.
    with librarian(tmp_path) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)


def test_generator_publishes_batch(tmp_path: Path) -> None:
    _setup_workspace_with_target(tmp_path)

    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = quick_success(
        _node_block("thm:goal", "theorem", "Statement of the goal.", "A valid proof.")
    )

    r = _run(
        "--workspace", str(tmp_path),
        "generator",
        "--target", "thm:goal",
        "--mode", "fresh",
        "--codex-argv", " ".join(fake_codex_argv()),
        "--silent-timeout-s", "5.0",
        "--actor", "generator:test",
        env=env,
    )
    assert r.returncode == 0, r.stderr

    # Find the generator-emitted event (filename includes event_type).
    events = [
        p for p in (tmp_path / "events").rglob("*.json")
        if "generator.batch_committed" in p.name
    ]
    assert len(events) == 1, list((tmp_path / "events").rglob("*.json"))
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "generator.batch_committed"
    assert body["target"] == "thm:goal"
    assert body["payload"]["attempt_id"].startswith("gen-")


def test_generator_invalid_mode_exit_2(tmp_path: Path) -> None:
    """argparse choices=('fresh','repair') makes invalid mode exit 2."""
    _setup_workspace_with_target(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "generator",
        "--target", "thm:goal",
        "--mode", "xyz",
        "--codex-argv", " ".join(fake_codex_argv()),
    )
    assert r.returncode == 2
    # No job file created.
    assert not list((tmp_path / "runtime" / "jobs").glob("*.json"))
    # No generator event published.
    assert not [
        p for p in (tmp_path / "events").rglob("*.json")
        if "generator.batch_committed" in p.name
    ]


def test_generator_unknown_target_exits_nonzero(tmp_path: Path) -> None:
    _setup_workspace_with_target(tmp_path)
    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = quick_success(
        _node_block("thm:absent", "theorem", "S", "P")
    )
    r = _run(
        "--workspace", str(tmp_path),
        "generator",
        "--target", "thm:absent",
        "--mode", "fresh",
        "--codex-argv", " ".join(fake_codex_argv()),
        env=env,
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "absent" in r.stderr.lower()
