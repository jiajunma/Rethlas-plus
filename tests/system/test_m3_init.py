"""M3 — `rethlas init` system tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, check=False,
        cwd=str(cwd) if cwd else None,
    )


def test_init_creates_full_skeleton(tmp_path: Path) -> None:
    result = _run("--workspace", str(tmp_path), "init")
    assert result.returncode == 0, result.stderr
    for rel in [
        "events",
        "knowledge_base",
        "knowledge_base/nodes",
        "runtime/jobs",
        "runtime/logs",
        "runtime/locks",
        "runtime/state",
    ]:
        assert (tmp_path / rel).is_dir(), f"{rel} missing"
    assert (tmp_path / "rethlas.toml").is_file()
    assert (tmp_path / ".gitignore").is_file()


def test_init_writes_annotated_template_anchored_to_arch(tmp_path: Path) -> None:
    result = _run("--workspace", str(tmp_path), "init")
    assert result.returncode == 0, result.stderr

    # Parse the written config and check every §2.4 default.
    from common.config import load_config

    cfg = load_config(tmp_path / "rethlas.toml")
    assert cfg.scheduling.desired_pass_count == 3
    assert cfg.scheduling.generator_workers == 2
    assert cfg.scheduling.verifier_workers == 4
    assert cfg.scheduling.codex_silent_timeout_seconds == 1800
    assert cfg.dashboard.bind == "127.0.0.1:8765"

    # Each field has a non-empty `# ...` comment on the line immediately
    # above it (self-documenting template).
    raw_lines = (tmp_path / "rethlas.toml").read_text(encoding="utf-8").splitlines()
    field_keys = [
        "desired_pass_count",
        "generator_workers",
        "verifier_workers",
        "codex_silent_timeout_seconds",
        "bind",
    ]
    for key in field_keys:
        idx = next((i for i, ln in enumerate(raw_lines) if ln.lstrip().startswith(key + " ")), None)
        assert idx is not None, f"{key!r} missing from template"
        assert idx > 0, f"{key!r} is at line 0"
        prior = raw_lines[idx - 1].strip()
        assert prior.startswith("#") and len(prior) > 2, (
            f"{key!r} lacks a comment line above it: {raw_lines[idx-1]!r}"
        )


def test_init_force_overwrites_config(tmp_path: Path) -> None:
    """``--force`` overwrites rethlas.toml but never events/."""
    _run("--workspace", str(tmp_path), "init")
    (tmp_path / "rethlas.toml").write_text("# tampered\n", encoding="utf-8")
    # Drop a sentinel event so we can check events/ survives.
    sentinel = tmp_path / "events" / "keepme.txt"
    sentinel.write_text("truth", encoding="utf-8")

    result = _run("--workspace", str(tmp_path), "init", "--force")
    assert result.returncode == 0, result.stderr
    cfg_text = (tmp_path / "rethlas.toml").read_text(encoding="utf-8")
    assert "desired_pass_count" in cfg_text
    assert sentinel.read_text(encoding="utf-8") == "truth"


def test_init_without_force_refuses_when_config_present(tmp_path: Path) -> None:
    _run("--workspace", str(tmp_path), "init")
    result = _run("--workspace", str(tmp_path), "init")
    assert result.returncode != 0
    assert "--force" in result.stderr
