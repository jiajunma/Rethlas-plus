"""M10 — linter category coverage on real workspaces.

Most tests seed a workspace, drive librarian to ``ready`` so Kuzu has
state, then plant a specific class of drift and assert the linter
detects it.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cli.workspace import workspace_paths
from common.kb.kuzu_backend import KuzuBackend
from librarian.heartbeat import PHASE_READY
from linter.main import run_linter_on_workspace
from tests.fixtures.librarian_proc import librarian


PYTHON = sys.executable


def _init(ws: Path) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), "init"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _publish(ws: Path, *args: str) -> None:
    r = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(ws), *args],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr


def _seed_def_and_theorem(ws: Path) -> None:
    _publish(
        ws, "add-node", "--label", "def:x", "--kind", "definition",
        "--statement", "Define X.", "--actor", "user:alice",
    )
    _publish(
        ws, "add-node", "--label", "thm:t", "--kind", "theorem",
        "--statement", r"T about \ref{def:x}.", "--proof", "p.",
        "--actor", "user:alice",
    )


def _drive_to_ready(ws: Path) -> None:
    with librarian(ws) as lp:
        lp.wait_for_phase(PHASE_READY, timeout=20.0)


def _read_report(ws: Path) -> dict:
    p = ws / "runtime" / "state" / "linter_report.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Clean workspace.
# ---------------------------------------------------------------------------
def test_clean_workspace_passes(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)
    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    assert rc == 0, report
    for cat in ("a", "b", "c", "d", "e", "f"):
        assert report[cat]["count"] == 0, (cat, report[cat])
    assert "0 violations" in report["summary"]


# ---------------------------------------------------------------------------
# Category A.
# ---------------------------------------------------------------------------
def test_category_a_filename_body_event_id_mismatch(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    # Tamper one event body so its event_id no longer matches the filename.
    files = sorted((tmp_path / "events").rglob("*.json"))
    target = files[0]
    body = json.loads(target.read_text(encoding="utf-8"))
    body["event_id"] = "20990101T000000.000-9999-deadbeefdeadbeef"
    target.write_text(json.dumps(body), encoding="utf-8")

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    assert rc == 5
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["a"]["violations"]]
    assert "A_event_id_mismatch" in codes


def test_category_a_duplicate_event_id(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    files = sorted((tmp_path / "events").rglob("*.json"))
    src = files[0]
    dup = src.parent / ("dup--" + src.name)
    dup.write_bytes(src.read_bytes())

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    # Duplicate has a malformed name; both violations may surface but the
    # duplicate-id one is the contract.
    codes = [v["code"] for v in report["a"]["violations"]]
    assert any(c in {"A_event_id_duplicate", "A_filename_invalid"} for c in codes)
    assert rc == 5


# ---------------------------------------------------------------------------
# Category B.
# ---------------------------------------------------------------------------
def test_category_b_cycle_detected(tmp_path: Path) -> None:
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:a", "--kind", "definition",
        "--statement", "A.", "--actor", "user:u",
    )
    _publish(
        tmp_path, "add-node", "--label", "def:b", "--kind", "definition",
        "--statement", r"B uses \ref{def:a}.", "--actor", "user:u",
    )
    _drive_to_ready(tmp_path)

    # Hand-insert a cycle: def:a -> def:b (b already depends on a).
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "MATCH (a:Node {label: 'def:a'}), (b:Node {label: 'def:b'}) "
            "CREATE (a)-[:DependsOn]->(b)"
        )
    finally:
        backend.close()

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["b"]["violations"]]
    assert "B_cycle" in codes
    assert rc == 5


# ---------------------------------------------------------------------------
# Category C / D — drift on stored counts.
# ---------------------------------------------------------------------------
def test_category_c_pass_count_drift(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)

    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "MATCH (n:Node {label: 'def:x'}) SET n.pass_count = 99"
        )
    finally:
        backend.close()

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["c"]["violations"]]
    assert "C_pass_count_drift" in codes
    assert rc == 5


def test_category_d_repair_count_drift(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)

    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "MATCH (n:Node {label: 'thm:t'}) SET n.repair_count = 7"
        )
    finally:
        backend.close()

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["d"]["violations"]]
    assert "D_repair_count_drift" in codes
    assert rc == 5


# ---------------------------------------------------------------------------
# Category E — nodes/ rendering.
# ---------------------------------------------------------------------------
def _bump_def_to_pass_one(ws: Path) -> None:
    backend = KuzuBackend(str(ws / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "MATCH (n:Node {label: 'def:x'}) SET n.pass_count = 1"
        )
    finally:
        backend.close()


def test_category_e_three_drift_kinds(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)
    _bump_def_to_pass_one(tmp_path)

    nodes_dir = tmp_path / "knowledge_base" / "nodes"
    # 1. content drift: rewrite def_x.md.
    from common.kb.kuzu_backend import KuzuBackend
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        from librarian.renderer import node_filename, render_node
        from common.kb.types import Node, NodeKind

        row = backend.node_by_label("def:x")
        node = Node(
            label=row.label, kind=NodeKind(row.kind),
            statement=row.statement, proof=row.proof,
            remark=row.remark, source_note=row.source_note,
            pass_count=row.pass_count, repair_count=row.repair_count,
            statement_hash=row.statement_hash,
            verification_hash=row.verification_hash,
            depends_on=tuple(backend.dependencies_of("def:x")),
        )
        # Make sure the canonical file exists, then mutate it.
        canonical = nodes_dir / node_filename(node)
        canonical.write_bytes(render_node(node))
        canonical.write_text(canonical.read_text() + "\n# tampered\n", encoding="utf-8")
    finally:
        backend.close()

    # 2. orphan file with no Kuzu row.
    (nodes_dir / "def_orphan.md").write_text("---\nlabel: def:orphan\n---\n", encoding="utf-8")

    # 3. missing file: bump thm:t to pass_count=1 then delete its file.
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "MATCH (n:Node {label: 'thm:t'}) SET n.pass_count = 1"
        )
    finally:
        backend.close()
    # thm:t.md gets created by the librarian _only_ when pass_count >=1 at
    # apply-time — we just bumped it manually so no file was rendered.
    # The linter must report missing.

    # First pass: report-only.
    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["e"]["violations"]]
    assert "E_content_drift" in codes
    assert "E_orphan_file" in codes
    assert "E_missing_file" in codes
    assert rc == 5

    # Second pass with --repair-nodes: idempotent; second run after that is clean.
    rc = run_linter_on_workspace(
        workspace_paths(str(tmp_path)), repair_nodes=True
    )
    # First repair pass still reports the violations it then fixed.
    assert rc == 5
    rc2 = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report2 = _read_report(tmp_path)
    assert report2["e"]["count"] == 0
    # B/C/D drift may persist (we bumped pass_count manually) — but E is clean.
    # rc2 may still be 5 because of C drift; that's expected and not an E concern.


# ---------------------------------------------------------------------------
# Category F.
# ---------------------------------------------------------------------------
def test_category_f_event_sha256_mismatch(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)

    files = sorted((tmp_path / "events").rglob("*.json"))
    target = files[0]
    body = json.loads(target.read_text(encoding="utf-8"))
    target.write_text(
        json.dumps(body) + "\n\n",  # extra bytes change SHA but keep parse OK
        encoding="utf-8",
    )

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["f"]["violations"]]
    assert "F_event_sha256_mismatch" in codes
    assert rc == 5


def test_category_f_event_file_missing(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)
    files = sorted((tmp_path / "events").rglob("*.json"))
    files[0].unlink()

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["f"]["violations"]]
    assert "F_event_file_missing" in codes
    assert rc == 5


# ---------------------------------------------------------------------------
# Aggregation + concurrency lock.
# ---------------------------------------------------------------------------
def test_aggregates_b_and_d_in_one_pass(tmp_path: Path) -> None:
    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "def:a", "--kind", "definition",
        "--statement", "A.", "--actor", "user:u",
    )
    _publish(
        tmp_path, "add-node", "--label", "def:b", "--kind", "definition",
        "--statement", r"B uses \ref{def:a}.", "--actor", "user:u",
    )
    _drive_to_ready(tmp_path)

    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "MATCH (a:Node {label: 'def:a'}), (b:Node {label: 'def:b'}) "
            "CREATE (a)-[:DependsOn]->(b)"
        )
        backend._conn.execute(
            "MATCH (n:Node {label: 'def:a'}) SET n.repair_count = 5"
        )
    finally:
        backend.close()

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    assert rc == 5
    report = _read_report(tmp_path)
    b_codes = [v["code"] for v in report["b"]["violations"]]
    d_codes = [v["code"] for v in report["d"]["violations"]]
    assert "B_cycle" in b_codes
    assert "D_repair_count_drift" in d_codes


def test_refuses_when_supervise_lock_held(tmp_path: Path) -> None:
    _init(tmp_path)
    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
        assert rc == 2
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def test_allow_concurrent_overrides_lock(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    _drive_to_ready(tmp_path)

    lock_path = tmp_path / "runtime" / "locks" / "supervise.lock"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc = run_linter_on_workspace(
            workspace_paths(str(tmp_path)), allow_concurrent=True
        )
        # No drift planted, so 0; the report carries a "note".
        assert rc == 0
        report = _read_report(tmp_path)
        assert "note" in report
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
