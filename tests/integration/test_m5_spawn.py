"""M5 — spawn helper plumbs RETHLAS_WORKSPACE + job_id positional."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from common.runtime.spawn import build_wrapper_env, spawn_wrapper


pytestmark = pytest.mark.timeout(15)


def test_build_wrapper_env_overlays_workspace(tmp_path: Path) -> None:
    env = build_wrapper_env(workspace=tmp_path)
    assert env["RETHLAS_WORKSPACE"] == str(tmp_path.resolve())
    # PATH from the parent process is inherited.
    assert "PATH" in env


def test_build_wrapper_env_passes_through_caller_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "stub-value-only")
    env = build_wrapper_env(workspace=tmp_path)
    assert env["OPENAI_API_KEY"] == "stub-value-only"


def test_extra_env_overrides_inherited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKER", "outer")
    env = build_wrapper_env(workspace=tmp_path, extra={"MARKER": "inner"})
    assert env["MARKER"] == "inner"


def test_spawn_wrapper_passes_workspace_and_job_id(tmp_path: Path) -> None:
    """Drive a tiny Python program that echoes argv + RETHLAS_WORKSPACE."""
    script = tmp_path / "echo_wrapper.py"
    script.write_text(
        "import os, sys\n"
        "print('JOB_ID=' + sys.argv[1])\n"
        "print('WORKSPACE=' + os.environ['RETHLAS_WORKSPACE'])\n"
        "print('MARKER=' + os.environ.get('MARKER','none'))\n",
        encoding="utf-8",
    )
    proc = spawn_wrapper(
        workspace=tmp_path,
        wrapper_argv=[sys.executable, str(script)],
        job_id="ver-test-id",
        extra_env={"MARKER": "from-test"},
        stdout=subprocess.PIPE,
    )
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0
    text = out.decode("utf-8")
    assert "JOB_ID=ver-test-id" in text
    assert f"WORKSPACE={tmp_path.resolve()}" in text
    assert "MARKER=from-test" in text
