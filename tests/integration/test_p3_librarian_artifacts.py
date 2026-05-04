from __future__ import annotations

import json
from pathlib import Path

from common.events.filenames import format_filename
from common.events.ids import allocate_event_id
from common.events.io import atomic_write_event
from tests.fixtures.librarian_proc import librarian
from tests.fixtures.tmp_workspace import make_workspace


def _write_event(ws: Path, body: dict) -> Path:
    event_id = body["event_id"]
    iso_ms, seq, uid = event_id.rsplit("-", 2)
    yyyymmdd = iso_ms[:8]
    shard = ws / "events" / f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    shard.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=iso_ms,
        event_type=body["type"],
        target=body.get("target"),
        actor=body["actor"],
        seq=int(seq),
        uid=uid,
    )
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return atomic_write_event(shard / fname, raw)


def test_librarian_admits_learner_batch_without_rendering_nodes(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    eid = allocate_event_id()
    body = {
        "event_id": eid.event_id,
        "type": "learner.batch_proposed",
        "actor": "learner:test",
        "ts": "2026-05-04T12:00:00.000+08:00",
        "payload": {
            "source_id": "src:toy",
            "learner_run": "learn_toy_001",
            "context_hash": "sha256:" + "a" * 64,
            "source_spans": [
                {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
            ],
            "notation_contexts": [],
            "candidate_nodes": [
                {
                    "label": "def:toy_object",
                    "kind": "definition",
                    "statement": "A toy object.",
                    "proof": "",
                    "remark": "",
                    "source_note": "",
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
        },
    }
    _write_event(tmp_path, body)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase("ready", timeout=20.0)

    artifact = tmp_path / "knowledge_base" / "phase3" / "learner_batches" / f"{eid.event_id}.json"
    assert artifact.is_file()
    assert not (tmp_path / "knowledge_base" / "nodes" / "def_toy_object.md").exists()


def test_librarian_writes_referee_report_under_reviews(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    eid = allocate_event_id()
    report = {
        "output_schema": "referee_report_v1",
        "review_id": "review_toy_001",
        "target": "thm:toy",
        "workspace_path": "reviews/review_toy_001",
        "target_hashes": {},
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
        "summary": "needs work",
    }
    body = {
        "event_id": eid.event_id,
        "type": "referee.review_completed",
        "actor": "referee:test",
        "ts": "2026-05-04T12:00:00.000+08:00",
        "target": "thm:toy",
        "payload": {
            "review_id": "review_toy_001",
            "workspace_path": "reviews/review_toy_001",
            "target_hashes": {},
            "verdict": "needs_revision",
            "issue_summary": {"issue_count": 1, "blocks_acceptance": True},
            "report_hash": "sha256:" + "b" * 64,
            "report": report,
        },
    }
    _write_event(tmp_path, body)
    with librarian(tmp_path) as lp:
        lp.wait_for_phase("ready", timeout=20.0)

    assert (tmp_path / "reviews" / "review_toy_001" / "review_completed.json").is_file()
    assert not any((tmp_path / "knowledge_base" / "nodes").glob("*.md"))
