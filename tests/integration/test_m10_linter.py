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
def test_category_a_envelope_invalid(tmp_path: Path) -> None:
    """§3.4 envelope-level validation surfaces unknown type / bad actor."""
    _init(tmp_path)
    _seed_def_and_theorem(tmp_path)
    files = sorted((tmp_path / "events").rglob("*.json"))
    target = files[0]
    body = json.loads(target.read_text(encoding="utf-8"))
    body["type"] = "rogue.event_type"  # not in §3.4 allowlist
    target.write_text(json.dumps(body), encoding="utf-8")

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    codes = [v["code"] for v in report["a"]["violations"]]
    assert "A_envelope_invalid" in codes
    assert rc == 5


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


def test_category_c_ignores_apply_failed_verdict(tmp_path: Path) -> None:
    """An ``apply_failed`` verdict whose payload.vh matches the current
    node must NOT be counted by the pass_count audit.

    Mirrors category D's behaviour. Without this, a hash-mismatch'd
    verifier verdict whose vh happens to match the node's current vh
    (e.g. after a revise-and-revert sequence) would phantom-bump the
    audit and surface a false-positive ``C_pass_count_drift``.
    """
    from common.events.filenames import format_filename
    from common.events.io import atomic_write_event
    from common.events.ids import allocate_event_id

    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "lem:x", "--kind", "lemma",
        "--statement", "S.", "--proof", "p.", "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)

    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        # Fresh lemma starts at pass_count=0. We plant ONE apply_failed
        # verdict at the current vh and assert the audit still returns
        # 0 (the buggy code path returned 1 because it filtered only by
        # vh-match without consulting AppliedEvent.status).
        res = backend._conn.execute(
            "MATCH (n:Node {label: 'lem:x'}) RETURN n.verification_hash, n.pass_count"
        )
        row = res.get_next()
        vh = row[0]
        stored = int(row[1])
    finally:
        backend.close()
    assert stored == 0, f"fresh lemma should start at pass_count=0, got {stored}"

    # Plant a verifier event in events/ whose payload.vh matches the
    # current node, and an AppliedEvent row marking it apply_failed.
    eid = allocate_event_id()
    body = {
        "event_id": eid.event_id,
        "type": "verifier.run_completed",
        "actor": "verifier:codex-test",
        "ts": "2026-04-26T00:00:00.000+00:00",
        "target": "lem:x",
        "payload": {
            "verdict": "accepted",
            "verification_hash": vh,
            "verification_report": {
                "summary": "ok", "checked_items": [], "gaps": [],
                "critical_errors": [], "external_reference_checks": [],
            },
            "repair_hint": "",
        },
    }
    shard = tmp_path / "events" / "2026-04-26"
    shard.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=eid.iso_ms,
        event_type="verifier.run_completed",
        target="lem:x",
        actor="verifier:codex-test",
        seq=eid.seq,
        uid=eid.uid,
    )
    atomic_write_event(shard / fname, json.dumps(body).encode("utf-8"))

    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "CREATE (a:AppliedEvent {event_id: $eid, status: 'apply_failed', "
            "reason: 'hash_mismatch', detail: 'simulated', "
            "event_sha256: 'deadbeef', applied_at: '2026-04-26T00:00:00.000Z', "
            "target_label: 'lem:x'})",
            {"eid": eid.event_id},
        )
    finally:
        backend.close()

    run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    c_codes = [v["code"] for v in report["c"]["violations"]]
    assert "C_pass_count_drift" not in c_codes, (
        f"audit incorrectly counted the apply_failed verdict; "
        f"violations={report['c']['violations']}"
    )


def test_category_c_ignores_pre_revision_verdicts(tmp_path: Path) -> None:
    """A user revision resets pass_count to ``initial_count``. A verdict
    that landed *before* the revision must not contribute to the audit
    even if its payload.vh happens to match the current vh (revise-and-
    revert pattern).
    """
    from common.events.filenames import format_filename
    from common.events.io import atomic_write_event
    from common.events.ids import allocate_event_id

    _init(tmp_path)
    _publish(
        tmp_path, "add-node", "--label", "lem:x", "--kind", "lemma",
        "--statement", "S.", "--proof", "p.", "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)

    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        res = backend._conn.execute(
            "MATCH (n:Node {label: 'lem:x'}) RETURN n.verification_hash"
        )
        vh = res.get_next()[0]
    finally:
        backend.close()

    # Plant a hand-rolled "applied" verdict at vh=current with
    # iso_ms < the upcoming revision's iso_ms.
    early_eid = allocate_event_id()
    early_body = {
        "event_id": early_eid.event_id,
        "type": "verifier.run_completed",
        "actor": "verifier:codex-test",
        "ts": "2026-04-26T00:00:00.000+00:00",
        "target": "lem:x",
        "payload": {
            "verdict": "accepted",
            "verification_hash": vh,
            "verification_report": {
                "summary": "ok", "checked_items": [], "gaps": [],
                "critical_errors": [], "external_reference_checks": [],
            },
            "repair_hint": "",
        },
    }
    shard = tmp_path / "events" / "2026-04-26"
    shard.mkdir(parents=True, exist_ok=True)
    early_fname = format_filename(
        iso_ms=early_eid.iso_ms,
        event_type="verifier.run_completed",
        target="lem:x",
        actor="verifier:codex-test",
        seq=early_eid.seq,
        uid=early_eid.uid,
    )
    atomic_write_event(shard / early_fname, json.dumps(early_body).encode("utf-8"))

    # Mark it applied in Kuzu so it would otherwise count toward the audit.
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        backend._conn.execute(
            "CREATE (a:AppliedEvent {event_id: $eid, status: 'applied', "
            "reason: '', detail: '', event_sha256: 'deadbeef', "
            "applied_at: '2026-04-26T00:00:00.000Z', target_label: 'lem:x'})",
            {"eid": early_eid.event_id},
        )
    finally:
        backend.close()

    # Now publish a node_revised with a *later* iso_ms — this is the
    # boundary. After replay, pass_count is reset to initial_count = 0.
    _publish(
        tmp_path, "revise-node", "--label", "lem:x", "--kind", "lemma",
        "--statement", "S.", "--proof", "p.", "--actor", "user:alice",
    )
    _drive_to_ready(tmp_path)

    # Stored pass_count should be 0 (revision reset).
    backend = KuzuBackend(str(tmp_path / "knowledge_base" / "dag.kz"))
    try:
        res = backend._conn.execute(
            "MATCH (n:Node {label: 'lem:x'}) RETURN n.pass_count"
        )
        stored = int(res.get_next()[0])
    finally:
        backend.close()
    assert stored == 0, f"expected stored pass_count=0 after revision, got {stored}"

    run_linter_on_workspace(workspace_paths(str(tmp_path)))
    report = _read_report(tmp_path)
    c_codes = [v["code"] for v in report["c"]["violations"]]
    assert "C_pass_count_drift" not in c_codes, (
        f"audit incorrectly counted a pre-revision verdict; "
        f"violations={report['c']['violations']}"
    )


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
