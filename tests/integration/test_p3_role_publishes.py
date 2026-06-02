from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from common.runtime.jobs import STATUS_PUBLISHING, STATUS_STARTING, utc_now_iso
from common.runtime.jobs_v2 import (
    RoleJobRecord,
    read_role_job_file,
    write_role_job_file,
)
from learner.role import main as learner_role
from referee.role import main as referee_role
from tests.fixtures.scripted_codex import fake_codex_argv, quick_success
from tests.fixtures.tmp_workspace import make_workspace


def _role_job(ws: Path, *, kind: str, target: str, input_packet: dict) -> str:
    job_id = f"{kind}-test"
    rec = RoleJobRecord(
        job_id=job_id,
        kind=kind,
        mode="learn_source_spans" if kind == "learner" else "review_target",
        target=target,
        context_hash="sha256:" + "a" * 64,
        dispatch_hash="sha256:" + "a" * 64,
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
        status=STATUS_STARTING,
        log_path=f"runtime/logs/{job_id}.codex.log",
        input=input_packet,
    )
    write_role_job_file(ws / "runtime" / "jobs" / f"{job_id}.json", rec)
    return job_id


def _learner_json() -> str:
    return json.dumps(
        {
            "output_schema": "learner_batch_v1",
            "source_id": "src:toy",
            "run_id": "learn_toy_001",
            "context_hash": "sha256:" + "a" * 64,
            "source_spans": [
                {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
            ],
            "notation_contexts": [],
            "candidate_nodes": [
                {
                    "label": "def:toy_object",
                    "kind": "definition",
                    "statement": "A toy object is defined here.",
                    "proof": "",
                    "remark": "",
                    "source_note": "toy",
                    "source_refs": [
                        {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                    ],
                }
            ],
            "dependency_edges": [],
            "bridge_requests": [],
            "verification_requests": [
                {"target": "def:toy_object", "kind": "verify_definition"}
            ],
            "issues": [],
            "summary": "ok",
        }
    )


def _referee_json() -> str:
    return json.dumps(
        {
            "output_schema": "referee_report_v1",
            "review_id": "review_toy_001",
            "target": "thm:toy",
            "workspace_path": "reviews/review_toy_001",
            "target_hashes": {"statement_hash": "sha256:" + "a" * 64},
            "verdict": "needs_revision",
            "checked_claims": [],
            "reconstructed_jumps": [],
            "generated_repairs": [],
            "verified_repairs": [],
            "unresolved_gaps": [],
            "requested_details": [],
            "issues": [{"severity": "major", "issue": "missing lemma"}],
            "counterexample_attempts": [],
            "external_reference_checks": [],
            "extraction_quality_checks": [],
            "recommended_kb_updates": [],
            "summary": "needs details",
        }
    )


def test_learner_role_publishes_batch_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_workspace(tmp_path, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", quick_success(_learner_json()))
    job_id = _role_job(tmp_path, kind="learner", target="src:toy#spans", input_packet={"source_id": "src:toy"})

    rc = learner_role([
        job_id,
        "--codex-argv",
        " ".join(fake_codex_argv()),
        "--silent-timeout-s",
        "5.0",
        "--actor",
        "learner:test",
    ])
    assert rc == 0
    rec = read_role_job_file(tmp_path / "runtime" / "jobs" / f"{job_id}.json")
    assert rec is not None and rec.status == STATUS_PUBLISHING
    events = list((tmp_path / "events").rglob("*.json"))
    assert len(events) == 1
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "learner.batch_proposed"
    assert body["payload"]["candidate_nodes"][0]["label"] == "def:toy_object"


def test_referee_role_publishes_report_and_reviews_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_workspace(tmp_path, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", quick_success(_referee_json()))
    job_id = _role_job(tmp_path, kind="referee", target="thm:toy", input_packet={"target": "thm:toy"})

    rc = referee_role([
        job_id,
        "--codex-argv",
        " ".join(fake_codex_argv()),
        "--silent-timeout-s",
        "5.0",
        "--actor",
        "referee:test",
    ])
    assert rc == 0
    events = list((tmp_path / "events").rglob("*.json"))
    assert len(events) == 1
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "referee.review_completed"
    assert (tmp_path / "reviews" / "review_toy_001" / "review_completed.json").is_file()
    assert not (tmp_path / "knowledge_base" / "nodes" / "review_toy_001.md").exists()


def test_referee_role_salvages_valid_report_from_failed_codex_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_workspace(tmp_path, seed_config=True)
    monkeypatch.setenv("RETHLAS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "FAKE_CODEX_SCRIPT",
        json.dumps(
            {
                "stdout_lines": [{"text": _referee_json(), "delay_s": 0.0}],
                "stderr_lines": [{"text": "ERROR: Reconnecting... 1/5", "delay_s": 0.0}],
                "exit_code": 1,
            }
        ),
    )
    job_id = _role_job(tmp_path, kind="referee", target="thm:toy", input_packet={"target": "thm:toy"})

    rc = referee_role([
        job_id,
        "--codex-argv",
        " ".join(fake_codex_argv()),
        "--silent-timeout-s",
        "5.0",
        "--actor",
        "referee:test",
    ])

    assert rc == 0
    rec = read_role_job_file(tmp_path / "runtime" / "jobs" / f"{job_id}.json")
    assert rec is not None and rec.status == STATUS_PUBLISHING
    assert "salvaged_from_log" in rec.detail
    events = list((tmp_path / "events").rglob("*.json"))
    assert len(events) == 1
    body = json.loads(events[0].read_text(encoding="utf-8"))
    assert body["type"] == "referee.review_completed"
    assert body["payload"]["review_id"] == "review_toy_001"
