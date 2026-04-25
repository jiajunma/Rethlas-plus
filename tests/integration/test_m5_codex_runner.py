"""M5 — codex_runner integration tests + fake_codex sanity."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from common.runtime.codex_runner import run_codex
from tests.fixtures.scripted_codex import (
    crash,
    fake_codex_argv,
    quick_success,
    silent_for,
    streaming_lines,
    stderr_warning,
)


pytestmark = pytest.mark.timeout(30)


def test_quick_success_exit_zero(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "ver-x.codex.log"
    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = quick_success("hello")
    out = run_codex(
        argv=fake_codex_argv(),
        log_path=log,
        silent_timeout_s=5.0,
        poll_interval_s=0.05,
        env=env,
    )
    assert out.exit_code == 0
    assert not out.timed_out
    assert "hello" in log.read_text(encoding="utf-8")


def test_crash_returns_nonzero_exit(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "gen-y.codex.log"
    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = crash(exit_code=2, stderr="explode")
    out = run_codex(
        argv=fake_codex_argv(),
        log_path=log,
        silent_timeout_s=5.0,
        poll_interval_s=0.05,
        env=env,
    )
    assert out.exit_code == 2
    assert "explode" in log.read_text(encoding="utf-8")


def test_log_merges_stdout_and_stderr(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "ver-z.codex.log"
    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = stderr_warning("stderr-warn", stdout="stdout-body")
    out = run_codex(
        argv=fake_codex_argv(),
        log_path=log,
        silent_timeout_s=5.0,
        poll_interval_s=0.05,
        env=env,
    )
    assert out.ok
    text = log.read_text(encoding="utf-8")
    assert "stderr-warn" in text
    assert "stdout-body" in text


def test_log_mtime_timeout_marks_timed_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "logs" / "gen-t.codex.log"
    env = os.environ.copy()
    # Sleep way longer than the timeout — no log activity → kill.
    env["FAKE_CODEX_SCRIPT"] = silent_for(30.0)
    # Apply scale to BOTH the parent (so run_codex sees it) and the
    # child (so the fake codex sleeps for the scaled wall-clock).
    monkeypatch.setenv("RETHLAS_TEST_TIME_SCALE", "0.05")  # 30 * 0.05 = 1.5s
    env["RETHLAS_TEST_TIME_SCALE"] = "0.05"
    env["FAKE_CODEX_TIME_SCALE"] = "1.0"
    out = run_codex(
        argv=fake_codex_argv(),
        log_path=log,
        silent_timeout_s=5.0,  # × 0.05 = 0.25s real
        poll_interval_s=0.05,
        env=env,
    )
    assert out.timed_out
    # Returncode reflects SIGKILL / SIGINT.
    assert out.exit_code != 0


def test_streaming_keeps_log_fresh_and_avoids_timeout(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "ver-stream.codex.log"
    env = os.environ.copy()
    env["FAKE_CODEX_SCRIPT"] = streaming_lines(
        ["chunk1", "chunk2", "chunk3", "chunk4"], delay_s=0.1
    )
    env["RETHLAS_TEST_TIME_SCALE"] = "1.0"
    out = run_codex(
        argv=fake_codex_argv(),
        log_path=log,
        silent_timeout_s=2.0,
        poll_interval_s=0.05,
        env=env,
    )
    assert out.ok, out
    body = log.read_text(encoding="utf-8")
    for chunk in ("chunk1", "chunk2", "chunk3", "chunk4"):
        assert chunk in body


def test_env_passthrough_to_child(tmp_path: Path) -> None:
    """fake_codex echoes its env via stderr if asked — we provide a custom var."""
    log = tmp_path / "logs" / "envcheck.codex.log"
    # Use python -c to dump an env var to stdout — that uses fake_codex's
    # invocation contract minus the script. Wrappers care primarily that
    # custom env vars REACH the child; we verify by calling python -c directly.
    out = run_codex(
        argv=[sys.executable, "-c", "import os; print(os.environ['MARKER'])"],
        log_path=log,
        silent_timeout_s=5.0,
        poll_interval_s=0.05,
        env={**os.environ, "MARKER": "passed-through-correctly"},
    )
    assert out.ok
    assert "passed-through-correctly" in log.read_text(encoding="utf-8")
