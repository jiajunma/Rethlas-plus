"""M3 — user publish CLI system tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True, text=True, check=False,
    )


def _init(ws: Path) -> None:
    r = _run("--workspace", str(ws), "init")
    assert r.returncode == 0, r.stderr


def _events(ws: Path) -> list[Path]:
    return sorted((ws / "events").rglob("*.json"))


def test_add_node_writes_canonical_event_file(tmp_path: Path) -> None:
    _init(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "def:primary_object",
        "--kind", "definition",
        "--statement", "A primary object is ...",
        "--actor", "user:alice",
    )
    # Not applied (no librarian running), but the CLI must exit 0 with
    # the "queued, supervise not running" message (§9.1 D2).
    assert r.returncode == 0, r.stderr
    assert "queued" in r.stdout.lower() or "applied" in r.stdout.lower()

    events = _events(tmp_path)
    assert len(events) == 1, events
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "user.node_added"
    assert body["target"] == "def:primary_object"
    assert body["actor"] == "user:alice"
    assert body["payload"]["kind"] == "definition"


def test_add_node_external_theorem_requires_source_note(tmp_path: Path) -> None:
    _init(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "ext:riemann",
        "--kind", "external_theorem",
        "--statement", "Riemann hypothesis",
        "--actor", "user:alice",
    )
    assert r.returncode != 0
    rejects = (tmp_path / "runtime/state/rejected_writes.jsonl")
    assert rejects.is_file()
    entry = json.loads(rejects.read_text(encoding="utf-8").splitlines()[-1])
    assert "source_note" in entry["detail"]
    assert not list((tmp_path / "events").rglob("*.json"))


def test_add_node_placeholder_label_rejected(tmp_path: Path) -> None:
    _init(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "thm:main",  # placeholder
        "--kind", "theorem",
        "--statement", "T",
        "--actor", "user:alice",
    )
    assert r.returncode != 0
    rejects = (tmp_path / "runtime/state/rejected_writes.jsonl")
    assert rejects.is_file()
    entry = json.loads(rejects.read_text(encoding="utf-8").splitlines()[-1])
    assert "placeholder" in entry["detail"].lower()
    assert not list((tmp_path / "events").rglob("*.json"))


def test_add_node_prefix_mismatch_rejected(tmp_path: Path) -> None:
    _init(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "thm:foo",
        "--kind", "lemma",  # prefix mismatch
        "--statement", "S",
        "--actor", "user:alice",
    )
    assert r.returncode != 0, "prefix/kind mismatch must be rejected"
    rejects = (tmp_path / "runtime/state/rejected_writes.jsonl")
    assert rejects.is_file()
    line = rejects.read_text(encoding="utf-8").splitlines()[-1]
    entry = json.loads(line)
    assert "prefix" in entry["detail"].lower()
    assert not list((tmp_path / "events").rglob("*.json"))


def test_attach_hint_requires_non_empty_hint(tmp_path: Path) -> None:
    _init(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "attach-hint",
        "--target", "lem:x",
        "--hint", "   ",  # whitespace only
        "--actor", "user:alice",
    )
    assert r.returncode != 0


def test_producers_toml_actor_pattern_enforced(tmp_path: Path) -> None:
    """Admission rejects events whose actor doesn't match producers.toml."""
    _init(tmp_path)
    r = _run(
        "--workspace", str(tmp_path),
        "add-node",
        "--label", "def:x",
        "--kind", "definition",
        "--statement", "s",
        "--actor", "librarian:xyz",  # not in producers.toml user kinds
    )
    assert r.returncode != 0
    rejects = (tmp_path / "runtime/state/rejected_writes.jsonl")
    assert rejects.is_file()
    assert not list((tmp_path / "events").rglob("*.json"))


def test_uninitialized_workspace_exits_2(tmp_path: Path) -> None:
    """Running add-node against a fresh (uninitialized) dir exits code 2."""
    bare = tmp_path / "bare"
    bare.mkdir()
    r = _run(
        "--workspace", str(bare),
        "add-node",
        "--label", "def:x",
        "--kind", "definition",
        "--statement", "s",
    )
    assert r.returncode == 2
    assert "not initialized" in r.stderr.lower() or "not initialised" in r.stderr.lower()


def test_workspace_flag_universality(tmp_path: Path) -> None:
    """``--workspace <path>`` plumbs through init + add-node + rebuild."""
    alt = tmp_path / "alt"
    r = _run("--workspace", str(alt), "init")
    assert r.returncode == 0, r.stderr
    assert (alt / "events").is_dir()
    assert (alt / "rethlas.toml").is_file()
    # Now add-node under the SAME --workspace; event file must live
    # under alt/events/, not cwd.
    r = _run(
        "--workspace", str(alt),
        "add-node",
        "--label", "def:x",
        "--kind", "definition",
        "--statement", "s",
    )
    assert r.returncode == 0, r.stderr
    events = list((alt / "events").rglob("*.json"))
    assert len(events) == 1
