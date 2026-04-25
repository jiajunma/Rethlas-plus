"""M7 — verifier role wraps Codex, decodes verdict, publishes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from common.runtime.jobs import (
    JobRecord,
    STATUS_PUBLISHING,
    STATUS_STARTING,
    job_file_path,
    read_job_file,
    utc_now_iso,
    write_job_file,
)
from tests.fixtures.scripted_codex import fake_codex_argv, quick_success
from tests.fixtures.tmp_workspace import make_workspace
from verifier.role import main as role_main


_DISPATCH_HASH = "sha256:" + ("d" * 64)


def _seed_job(ws_root: Path) -> str:
    job_id = "ver-test"
    rec = JobRecord(
        job_id=job_id,
        kind="verifier",
        target="thm:goal",
        mode="single",
        dispatch_hash=_DISPATCH_HASH,
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_STARTING,
        log_path=f"runtime/logs/{job_id}.codex.log",
        target_kind="theorem",
        statement="Statement S",
        proof="Proof P",
    )
    write_job_file(job_file_path(ws_root / "runtime" / "jobs", job_id), rec)
    return job_id


def _verdict_json(verdict: str = "accepted") -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "verification_hash": "sha256:" + ("z" * 64),  # different from dispatch_hash
            "verification_report": {
                "summary": "ok",
                "checked_items": [],
                "gaps": [] if verdict == "accepted" else [{"step": 1, "reason": "x"}],
                "critical_errors": [],
                "external_reference_checks": [],
            },
            "repair_hint": "",
        }
    )


def test_role_publishes_verdict_with_dispatch_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§6.3: emitted verification_hash MUST equal dispatch_hash, not Codex's value."""
    ws_root = tmp_path
    make_workspace(ws_root, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(ws_root))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", quick_success(_verdict_json("accepted")))

    job_id = _seed_job(ws_root)
    rc = role_main([
        job_id,
        "--codex-argv", " ".join(fake_codex_argv()),
        "--silent-timeout-s", "5.0",
        "--actor", "verifier:test",
    ])
    assert rc == 0

    rec = read_job_file(job_file_path(ws_root / "runtime" / "jobs", job_id))
    assert rec is not None
    assert rec.status == STATUS_PUBLISHING

    events = sorted((ws_root / "events").rglob("*.json"))
    assert len(events) == 1
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "verifier.run_completed"
    assert body["target"] == "thm:goal"
    payload = body["payload"]
    assert payload["verdict"] == "accepted"
    # The crucial assertion: emitted hash equals dispatch hash, NOT what
    # Codex returned. This makes the apply-time hash-match gate the only
    # source of truth for staleness detection.
    assert payload["verification_hash"] == _DISPATCH_HASH
    # All 5 verification_report subfields present.
    for k in ("summary", "checked_items", "gaps", "critical_errors", "external_reference_checks"):
        assert k in payload["verification_report"]


def test_role_malformed_verdict_marks_crashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_root = tmp_path
    make_workspace(ws_root, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(ws_root))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", quick_success("not a verdict at all"))

    job_id = _seed_job(ws_root)
    rc = role_main([
        job_id,
        "--codex-argv", " ".join(fake_codex_argv()),
        "--silent-timeout-s", "5.0",
    ])
    assert rc != 0
    assert not list((ws_root / "events").rglob("*.json"))
    rejects = ws_root / "runtime" / "state" / "rejected_writes.jsonl"
    assert rejects.is_file()
    line = rejects.read_text(encoding="utf-8").splitlines()[-1]
    entry = json.loads(line)
    assert entry["reason"] == "no_verdict_json"
