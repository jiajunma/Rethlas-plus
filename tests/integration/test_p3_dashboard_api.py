from __future__ import annotations

from pathlib import Path

from common.phase3.artifacts import write_json_atomic
from dashboard.server import DashboardCore
from tests.fixtures.tmp_workspace import make_workspace


def test_dashboard_phase3_learner_and_review_endpoints(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    write_json_atomic(
        tmp_path / "knowledge_base" / "phase3" / "learner_batches" / "evt1.json",
        {
            "schema": "rethlas-learner-batch-artifact-v1",
            "event_id": "evt1",
            "event_type": "learner.batch_proposed",
            "actor": "learner:test",
            "ts": "2026-05-04T12:00:00.000+08:00",
            "source_id": "src:toy",
            "learner_run": "learn_toy_001",
            "context_hash": "sha256:a",
            "status": "proposed",
            "payload": {
                "candidate_nodes": [
                    {
                        "kind": "definition",
                        "label": "def:x",
                        "statement": "x is a toy definition.",
                        "proof": "",
                        "proof_status": "",
                        "proof_steps": [],
                        "depends_on": [],
                        "source_refs": [
                            {
                                "span_id": "span:toy:x",
                                "span_hash": "sha256:span",
                            }
                        ],
                    }
                ],
                "issues": [
                    {
                        "issue_id": "issue:toy",
                        "issue_type": "needs_visual_check",
                    }
                ],
            },
            "hash": "sha256:b",
        },
    )
    write_json_atomic(
        tmp_path / "reviews" / "evt2.json",
        {
            "schema": "rethlas-referee-artifact-v1",
            "event_id": "evt2",
            "event_type": "referee.review_completed",
            "actor": "referee:test",
            "ts": "2026-05-04T12:00:00.000+08:00",
            "target": "thm:toy",
            "payload": {
                "review_id": "review_toy_001",
                "verdict": "needs_revision",
                "issue_summary": {"issue_count": 1, "blocks_acceptance": True},
            },
            "hash": "sha256:c",
            "artifact_path": str(tmp_path / "reviews" / "review_toy_001" / "review_completed.json"),
        },
    )

    core = DashboardCore(tmp_path)
    runs = core.learner_runs()
    assert runs["count"] == 1
    assert runs["runs"][0]["candidate_count"] == 1
    assert core.learner_run("learn_toy_001") is not None

    study_kb = core.study_kb()
    assert study_kb["count"] == 1
    assert study_kb["batch_count"] == 1
    assert study_kb["kind_counts"] == {"definition": 1}
    assert study_kb["issue_counts_by_type"] == {"needs_visual_check": 1}
    assert study_kb["nodes"][0]["label"] == "def:x"
    assert study_kb["nodes"][0]["proof"] == ""
    assert study_kb["nodes"][0]["proof_steps"] == []
    assert study_kb["nodes"][0]["depends_on"] == []
    assert study_kb["nodes"][0]["span_ids"] == ["span:toy:x"]
    assert study_kb["nodes"][0]["batch_issue_count"] == 1

    reviews = core.reviews()
    assert reviews["count"] == 1
    assert reviews["reviews"][0]["review_id"] == "review_toy_001"
    assert reviews["reviews"][0]["issue_summary"]["blocks_acceptance"] is True
    assert core.review("review_toy_001") is not None
