from __future__ import annotations

from pathlib import Path

from common.phase3.artifacts import (
    write_bridge_repair_artifact,
    write_json_atomic,
    write_review_node_repair_artifact,
)
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
                        "extraction_kind": "implicit_paragraph",
                        "source_locator": "Section 1, opening paragraph",
                        "source_note": "The paragraph introduces x as durable terminology.",
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
                "report": {
                    "theorem_nodes": [
                        {
                            "label": "thm:toy",
                            "kind": "theorem",
                            "status": "gap",
                            "source_excerpt": "Theorem. If $x$ holds, then $y$ follows.",
                            "formula_excerpt": "$x \\Rightarrow y$",
                            "display_source_excerpt": "Theorem. If \\(x\\) holds, then \\(y\\) follows.",
                            "display_formula_excerpt": "\\[x \\Rightarrow y\\]",
                            "typesetting_notes": "TeX display field used for dashboard rendering.",
                        },
                        {
                            "label": "rem:toy",
                            "kind": "remark",
                            "status": "context",
                        },
                    ],
                    "theorem_dependency_edges": [
                        {
                            "dependency": "rem:toy",
                            "dependent": "thm:toy",
                            "relation": "uses",
                        }
                    ],
                    "node_location_notes": [
                        {
                            "label": "thm:toy",
                            "locator": "PDF p. 10",
                            "note": "main claim",
                        }
                    ],
                    "typo_findings": [
                        {
                            "typo_id": "typo:toy",
                            "severity": "minor",
                        }
                    ],
                },
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
    assert study_kb["count"] == 3
    assert study_kb["batch_count"] == 1
    assert study_kb["review_count"] == 1
    assert study_kb["kind_counts"] == {"definition": 1, "remark": 1, "theorem": 1}
    assert study_kb["issue_counts_by_type"] == {"needs_visual_check": 1}
    study_nodes = {n["label"]: n for n in study_kb["nodes"]}
    assert study_nodes["def:x"]["proof"] == ""
    assert study_nodes["def:x"]["proof_steps"] == []
    assert study_nodes["def:x"]["depends_on"] == []
    assert study_nodes["def:x"]["extraction_kind"] == "implicit_paragraph"
    assert study_nodes["def:x"]["source_locator"] == "Section 1, opening paragraph"
    assert study_nodes["def:x"]["span_ids"] == ["span:toy:x"]
    assert study_nodes["def:x"]["batch_issue_count"] == 1
    assert study_nodes["thm:toy"]["source_type"] == "referee_graph"
    assert study_nodes["thm:toy"]["review_id"] == "review_toy_001"
    assert study_nodes["thm:toy"]["source_excerpt"].startswith("Theorem.")
    assert study_nodes["thm:toy"]["formula_excerpt"] == "$x \\Rightarrow y$"
    assert study_nodes["thm:toy"]["display_formula_excerpt"] == "\\[x \\Rightarrow y\\]"
    assert study_nodes["thm:toy"]["typesetting_notes"].startswith("TeX display")
    assert study_kb["entry_count"] == 3
    entries = {entry["label"]: entry for entry in study_kb["entries"]}
    entry = entries["def:x"]
    assert entry["label"] == "def:x"
    assert entry["knowledge_status"] == "proposed"
    assert entry["review_status"] == "unreviewed"
    assert entry["version_count"] == 1
    assert entry["extraction_kind"] == "implicit_paragraph"
    assert entry["source_locator"] == "Section 1, opening paragraph"
    assert entry["latest_version"]["event_id"] == "evt1"
    assert entries["thm:toy"]["knowledge_status"] == "needs_revision"
    assert entries["thm:toy"]["review_status"] == "blocks_acceptance"
    assert entries["thm:toy"]["latest_review"]["review_id"] == "review_toy_001"
    assert entries["thm:toy"]["source_type"] == "referee_graph"

    reviews = core.reviews()
    assert reviews["count"] == 1
    assert reviews["reviews"][0]["review_id"] == "review_toy_001"
    assert reviews["reviews"][0]["issue_summary"]["blocks_acceptance"] is True
    assert reviews["reviews"][0]["theorem_node_count"] == 2
    assert reviews["reviews"][0]["dependency_edge_count"] == 1
    assert reviews["reviews"][0]["typo_count"] == 1
    assert core.review("review_toy_001") is not None
    graph = core.review_graph("review_toy_001")
    assert graph is not None
    assert graph["theorem_nodes"][0]["label"] == "thm:toy"
    assert graph["theorem_dependency_edges"][0]["relation"] == "uses"
    typos = core.review_typos("review_toy_001")
    assert typos is not None
    assert typos["typo_findings"][0]["typo_id"] == "typo:toy"

    write_review_node_repair_artifact(
        tmp_path,
        body={
            "repair_id": "repair_review_toy_001",
            "review_id": "review_toy_001",
            "target": "thm:toy",
            "actor": "review-repair:test",
            "ts": "2026-05-04T12:01:00.000+08:00",
            "repair_kind": "source_node_display",
            "output_schema": "source_node_repair_v1",
            "node_repairs": [
                {
                    "label": "thm:toy",
                    "source_excerpt": "Theorem repaired. If $x$ holds, then $y$ follows.",
                    "display_source_excerpt": "Theorem repaired. If \\(x\\) holds, then \\(y\\) follows.",
                    "display_formula_excerpt": "\\[x \\Rightarrow y\\]",
                    "typesetting_notes": "overlay applied",
                    "repair_actions": ["boundary", "typesetting"],
                }
            ],
            "summary": {"node_count": 1},
        },
    )
    repaired_kb = core.study_kb()
    repaired = {n["label"]: n for n in repaired_kb["nodes"]}["thm:toy"]
    assert repaired["source_excerpt"].startswith("Theorem repaired.")
    assert repaired["has_repair_overlay"] is True
    assert repaired["repair_id"] == "repair_review_toy_001"
    assert core.reviews()["reviews"][0]["repair_count"] == 1
    assert core.reviews()["reviews"][0]["repair_version_count"] == 1
    repair_history = core.review_repairs("review_toy_001")
    assert repair_history is not None
    assert repair_history["repairs"][0]["active"] is True
    repaired_graph = core.review_graph("review_toy_001")
    assert repaired_graph is not None
    assert repaired_graph["theorem_nodes"][0]["has_repair_overlay"] is True


def test_dashboard_study_kb_includes_referee_graph_without_learner_batch(
    tmp_path: Path,
) -> None:
    make_workspace(tmp_path, seed_config=True)
    write_json_atomic(
        tmp_path / "reviews" / "evt-review.json",
        {
            "schema": "rethlas-referee-artifact-v1",
            "event_id": "evt-review",
            "event_type": "referee.review_completed",
            "actor": "referee:test",
            "ts": "2026-05-04T12:00:00.000+08:00",
            "target": "src:toy",
            "payload": {
                "review_id": "review_toy_graph",
                "verdict": "major_gap",
                "issue_summary": {"issue_count": 2, "blocks_acceptance": True},
                "report": {
                    "theorem_nodes": [
                        {
                            "label": "src:toy:remark:intro",
                            "kind": "remark",
                            "status": "context",
                            "extraction_kind": "implicit_paragraph",
                            "source_locator": "PDF p. 1",
                            "source_note": "This paragraph functions as context.",
                            "statement": "The introduction states the conditional setup.",
                            "title": "Intro context",
                        },
                        {
                            "label": "src:toy:theorem:main",
                            "kind": "theorem",
                            "status": "gap",
                            "extraction_kind": "explicit_environment",
                            "source_locator": "PDF p. 2",
                            "source_note": "Main theorem depends on the setup.",
                            "statement": "The main theorem is conditional.",
                            "source_excerpt": "Theorem. For $x$, the map $f:x\\to y$ is bijective.",
                            "formula_excerpt": "$f:x\\to y$",
                            "display_source_excerpt": "Theorem. For \\(x\\), the map \\(f:x\\to y\\) is bijective.",
                            "display_formula_excerpt": "\\[f:x\\to y\\]",
                            "title": "Main theorem",
                        },
                    ],
                    "theorem_dependency_edges": [
                        {
                            "dependency": "src:toy:remark:intro",
                            "dependent": "src:toy:theorem:main",
                            "relation": "uses",
                        }
                    ],
                },
            },
            "hash": "sha256:review",
        },
    )

    core = DashboardCore(tmp_path)
    kb = core.study_kb()
    assert kb["count"] == 2
    assert kb["entry_count"] == 2
    assert kb["batch_count"] == 0
    assert kb["review_count"] == 1
    entries = {entry["label"]: entry for entry in kb["entries"]}
    assert entries["src:toy:theorem:main"]["source_type"] == "referee_graph"
    assert entries["src:toy:theorem:main"]["source_excerpt"].startswith("Theorem.")
    assert entries["src:toy:theorem:main"]["formula_excerpt"] == "$f:x\\to y$"
    assert entries["src:toy:theorem:main"]["display_formula_excerpt"] == "\\[f:x\\to y\\]"
    assert entries["src:toy:theorem:main"]["display_source_excerpt"].startswith("Theorem.")
    assert entries["src:toy:theorem:main"]["knowledge_status"] == "needs_revision"
    assert entries["src:toy:theorem:main"]["dependencies"] == [
        {"label": "src:toy:remark:intro", "relations": ["uses"]},
    ]
    assert entries["src:toy:remark:intro"]["dependents"] == [
        {"label": "src:toy:theorem:main", "relations": ["uses"]},
    ]
    graph = core.study_graph()
    assert graph["node_count"] == 2
    assert graph["edge_count"] == 1
    by_label = {node["label"]: node for node in graph["nodes"]}
    assert by_label["src:toy:theorem:main"]["review_id"] == "review_toy_graph"
    assert by_label["src:toy:theorem:main"]["proof_status"] == "gap"


def test_dashboard_phase3_study_graph_endpoint(tmp_path: Path) -> None:
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
                        "proof_status": "statement_only",
                        "proof_steps": [],
                        "depends_on": [],
                        "extraction_kind": "implicit_paragraph",
                        "source_locator": "Section 1, paragraph before Proposition Y",
                        "source_refs": [{"span_id": "span:toy:x"}],
                    },
                    {
                        "kind": "proposition",
                        "label": "prop:y",
                        "statement": "y follows from x.",
                        "proof": "Use x and a missing bridge.",
                        "proof_status": "proof_incomplete",
                        "proof_steps": [
                            {
                                "step": "Apply the local bridge.",
                                "depends_on": ["bridge_req_y"],
                            }
                        ],
                        "depends_on": ["def:x"],
                        "source_refs": [{"span_id": "span:toy:y"}],
                    },
                ],
                "dependency_edges": [
                    {
                        "dependency": "def:x",
                        "dependent": "prop:y",
                        "relation": "uses",
                    },
                    {
                        "from": "def:x",
                        "to": "prop:y",
                        "relation": "uses_alias",
                    }
                ],
                "bridge_requests": [
                    {
                        "request_id": "bridge_req_y",
                        "blocks": ["prop:y"],
                        "from_claim": "x is available.",
                        "to_claim": "y follows.",
                        "local_context_labels": ["def:x"],
                    }
                ],
            },
            "hash": "sha256:b",
        },
    )
    write_json_atomic(
        tmp_path / "knowledge_base" / "phase3" / "learner_batches" / "evt2.json",
        {
            "schema": "rethlas-learner-batch-artifact-v1",
            "event_id": "evt2",
            "event_type": "learner.batch_proposed",
            "actor": "learner:test",
            "ts": "2026-05-04T12:01:00.000+08:00",
            "source_id": "src:toy",
            "learner_run": "learn_toy_002",
            "context_hash": "sha256:c",
            "status": "proposed",
            "payload": {
                "candidate_nodes": [
                    {
                        "kind": "theorem",
                        "label": "thm:z",
                        "statement": "z is blocked.",
                        "proof": "",
                        "proof_status": "statement_only",
                        "proof_steps": [],
                        "depends_on": [],
                        "source_refs": [{"span_id": "span:toy:z"}],
                    },
                ],
                "dependency_edges": [],
                "bridge_requests": [
                    {
                        "label": "bridge:toy_external",
                        "from_label": "thm:z",
                        "kind": "external_reference_lookup",
                        "target": "[Toy, Lemma 1]",
                        "reason": "External reference needed.",
                    },
                ],
                "issues": [{"issue_id": "issue:toy", "issue_type": "missing_proof_step"}],
            },
            "hash": "sha256:d",
        },
    )
    write_json_atomic(
        tmp_path / "knowledge_base" / "phase3" / "learner_batches" / "evt3.json",
        {
            "schema": "rethlas-learner-batch-artifact-v1",
            "event_id": "evt3",
            "event_type": "learner.batch_proposed",
            "actor": "learner:test",
            "ts": "2026-05-04T12:02:00.000+08:00",
            "source_id": "src:toy",
            "learner_run": "learn_toy_003",
            "context_hash": "sha256:e",
            "status": "proposed",
            "payload": {
                "candidate_nodes": [
                    {
                        "kind": "proposition",
                        "label": "prop:y",
                        "statement": "y follows from x with the completed proof.",
                        "proof": "Completed proof.",
                        "proof_status": "source_proof_extracted",
                        "proof_steps": [
                            {"step": "Apply x.", "depends_on": ["def:x"]}
                        ],
                        "depends_on": ["def:x"],
                        "source_refs": [{"span_id": "span:toy:y2"}],
                    },
                ],
                "dependency_edges": [],
                "bridge_requests": [],
                "issues": [],
            },
            "hash": "sha256:e",
        },
    )

    graph = DashboardCore(tmp_path).study_graph()
    by_label = {n["label"]: n for n in graph["nodes"]}
    assert by_label["def:x"]["kind"] == "definition"
    assert by_label["def:x"]["extraction_kind"] == "implicit_paragraph"
    assert by_label["def:x"]["source_locator"] == "Section 1, paragraph before Proposition Y"
    assert by_label["prop:y"]["proof_status"] == "source_proof_extracted"
    assert by_label["bridge_req_y"]["kind"] == "bridge_request"
    assert by_label["bridge_req_y"]["proof_status"] == "needs_bridge"
    edges = {
        (e["source"], e["target"], e["relation"]): e for e in graph["edges"]
    }
    assert ("prop:y", "def:x", "depends_on") in edges
    assert ("prop:y", "def:x", "uses") in edges
    assert ("prop:y", "def:x", "uses_alias") in edges
    assert ("prop:y", "bridge_req_y", "proof_step") in edges
    assert ("prop:y", "bridge_req_y", "needs_bridge") in edges
    assert ("bridge_req_y", "def:x", "local_context") in edges
    assert ("thm:z", "bridge:toy_external", "needs_bridge") in edges
    assert by_label["bridge:toy_external"]["kind"] == "bridge_request"

    core = DashboardCore(tmp_path)
    search = core.study_search(q="follows")
    assert "prop:y" in {n["label"] for n in search["nodes"]}
    roots = core.study_roots()
    assert roots["nodes"][0]["label"] == "prop:y"
    detail = core.study_node("prop:y")
    assert detail is not None
    assert detail["version_count"] == 2
    assert {e["target"] for e in detail["dependencies"]} >= {"def:x"}
    assert "bridge_req_y" not in {e["target"] for e in detail["dependencies"]}
    assert {e["target"] for e in detail["bridges"]} == {"bridge_req_y"}
    assert detail["knowledge_status"] == "source_proof_extracted"
    neighborhood = core.study_neighborhood(root="prop:y", direction="dependencies", depth=1)
    assert {n["label"] for n in neighborhood["nodes"]} >= {"prop:y", "def:x", "bridge_req_y"}
    attention = core.study_attention()
    assert "bridge_req_y" in {n["label"] for n in attention["nodes"]}
    kb = core.study_kb()
    entries = {n["label"]: n for n in kb["entries"]}
    assert entries["prop:y"]["knowledge_status"] == "source_proof_extracted"
    assert entries["prop:y"]["proof_status"] == "source_proof_extracted"
    assert entries["prop:y"]["latest_version"]["event_id"] == "evt3"
    assert entries["prop:y"]["dependencies"] == [
        {
            "label": "def:x",
            "relations": ["depends_on", "proof_step", "uses", "uses_alias"],
        },
    ]
    assert entries["prop:y"]["dependency_count"] == 1
    assert entries["prop:y"]["bridges"] == [
        {"label": "bridge_req_y", "relations": ["needs_bridge", "proof_step"]},
    ]
    assert entries["prop:y"]["bridge_count"] == 1
    assert entries["def:x"]["dependents"] == [
        {
            "label": "prop:y",
            "relations": ["depends_on", "proof_step", "uses", "uses_alias"],
        },
    ]
    assert entries["bridge_req_y"]["bridge_users"] == [
        {"label": "prop:y", "relations": ["needs_bridge", "proof_step"]},
    ]
    assert entries["def:x"]["context_users"] == [
        {"label": "bridge_req_y", "relations": ["local_context"]},
    ]
    assert entries["bridge_req_y"]["context"] == [
        {"label": "def:x", "relations": ["local_context"]},
    ]
    discovery = core.study_discovery()
    assert discovery["source_span_count"] == 4
    assert discovery["uncovered_source_span_count"] == 0
    assert discovery["review_coverage"]["review_count"] == 0
    assert discovery["review_coverage"]["unreviewed_count"] >= 3


def test_dashboard_bridge_repair_overlay_redirects_and_closes(tmp_path: Path) -> None:
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
                        "kind": "external_theorem",
                        "label": "ext:old_main_theorem",
                        "statement": "The old source label states the main theorem.",
                        "proof": "",
                        "proof_status": "statement_only",
                        "proof_steps": [],
                        "depends_on": [],
                        "source_refs": [{"span_id": "span:toy:old"}],
                    },
                    {
                        "kind": "proposition",
                        "label": "prop:y",
                        "statement": "y follows from the main theorem.",
                        "proof": "Completed proof.",
                        "proof_status": "source_proof_extracted",
                        "proof_steps": [
                            {"step": "Use the main theorem.", "depends_on": ["ext:old_main_theorem"]}
                        ],
                        "depends_on": ["ext:old_main_theorem"],
                        "source_refs": [{"span_id": "span:toy:y"}],
                    },
                ],
                "dependency_edges": [
                    {
                        "dependency": "ext:old_main_theorem",
                        "dependent": "prop:y",
                        "relation": "uses",
                    }
                ],
                "bridge_requests": [
                    {
                        "request_id": "bridge_req_y",
                        "blocks": ["prop:y"],
                        "to_claim": "The bridge for y has now been absorbed by the source proof.",
                    }
                ],
            },
            "hash": "sha256:b",
        },
    )
    write_bridge_repair_artifact(
        tmp_path,
        body={
            "repair_id": "bridge_repair_toy",
            "actor": "bridge-repair:test",
            "ts": "2026-05-04T12:01:00.000Z",
            "active": True,
            "repair_kind": "bridge_overlay",
            "output_schema": "bridge_repair_v1",
            "actions": [
                {
                    "action": "redirect",
                    "bridge_id": "ext:old_main_theorem",
                    "source_label": "ext:old_main_theorem",
                    "target_label": "thm:new_main_theorem",
                    "reason": "canonical theorem label",
                },
                {
                    "action": "close",
                    "bridge_id": "bridge_req_y",
                    "target_labels": ["prop:y"],
                    "reason": "source proof extracted",
                },
            ],
            "summary": {"action_count": 2},
        },
    )

    core = DashboardCore(tmp_path)
    graph = core.study_graph()
    labels = {node["label"] for node in graph["nodes"]}
    assert "thm:new_main_theorem" in labels
    assert "ext:old_main_theorem" not in labels
    assert "bridge_req_y" not in labels
    by_label = {node["label"]: node for node in graph["nodes"]}
    assert by_label["thm:new_main_theorem"]["kind"] == "theorem"
    assert by_label["thm:new_main_theorem"]["aliases"] == ["ext:old_main_theorem"]
    edges = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}
    assert ("prop:y", "thm:new_main_theorem", "depends_on") in edges
    assert ("prop:y", "thm:new_main_theorem", "uses") in edges
    assert not any("bridge_req_y" in {source, target} for source, target, _ in edges)

    kb = core.study_kb()
    entries = {entry["label"]: entry for entry in kb["entries"]}
    assert "thm:new_main_theorem" in entries
    assert "ext:old_main_theorem" not in entries
    assert "bridge_req_y" not in entries
    assert entries["prop:y"]["bridges"] == []
    assert entries["prop:y"]["dependencies"] == [
        {
            "label": "thm:new_main_theorem",
            "relations": ["depends_on", "proof_step", "uses"],
        }
    ]
    assert kb["bridge_repair_summary"]["redirect_count"] == 1
    assert kb["bridge_repair_summary"]["close_count"] == 1
