from __future__ import annotations

import json

import pytest

from learner.decoder import LearnerDecodeError, parse_learner_batch


def _batch(**overrides):
    base = {
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
                "label": "def:zariski_open",
                "kind": "definition",
                "statement": "A Zariski open set is the complement of an algebraic set.",
                "proof": "",
                "remark": "",
                "source_note": "toy span",
                "source_refs": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
            }
        ],
        "dependency_edges": [],
        "bridge_requests": [],
        "verification_requests": [
            {"target": "def:zariski_open", "kind": "verify_definition"}
        ],
        "issues": [],
        "summary": "one definition",
    }
    base.update(overrides)
    return base


def test_learner_batch_accepts_source_backed_definition() -> None:
    parsed = parse_learner_batch(json.dumps(_batch()))
    assert parsed.source_id == "src:toy"
    assert parsed.candidate_nodes[0]["label"] == "def:zariski_open"
    assert parsed.event_payload()["learner_run"] == "learn_toy_001"


def test_learner_batch_rejects_missing_source_span_hash() -> None:
    data = _batch()
    del data["candidate_nodes"][0]["source_refs"][0]["span_hash"]
    with pytest.raises(LearnerDecodeError, match="missing_source_span_hash"):
        parse_learner_batch(json.dumps(data))


def test_learner_definition_requires_verify_definition() -> None:
    data = _batch(verification_requests=[])
    with pytest.raises(LearnerDecodeError, match="verification_request_required"):
        parse_learner_batch(json.dumps(data))


def test_learner_external_theorem_requires_verify_external_theorem() -> None:
    data = _batch(
        candidate_nodes=[
            {
                "label": "ext:classical_result",
                "kind": "external_theorem",
                "statement": "Classical result.",
                "proof": "",
                "remark": "",
                "source_note": "paper",
                "source_refs": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
            }
        ],
        verification_requests=[],
    )
    with pytest.raises(LearnerDecodeError, match="verify_external_theorem"):
        parse_learner_batch(json.dumps(data))


def test_learner_proof_requiring_candidate_gets_proof_status() -> None:
    data = _batch(
        candidate_nodes=[
            {
                "label": "lem:toy_step",
                "kind": "lemma",
                "statement": "Toy step.",
                "proof": "Apply the definition and simplify.",
                "remark": "",
                "source_note": "paper",
                "source_refs": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
                "proof_steps": [{"step": "Apply the definition."}],
                "depends_on": ["def:zariski_open"],
            }
        ],
        verification_requests=[
            {"target": "def:zariski_open", "kind": "verify_definition"}
        ],
    )

    parsed = parse_learner_batch(json.dumps(data))
    node = parsed.candidate_nodes[0]

    assert node["proof_status"] == "proof_sketch_extracted"
    assert node["proof_steps"][0]["step"] == "Apply the definition."
    assert node["depends_on"] == ["def:zariski_open"]


def test_learner_empty_proof_candidate_is_statement_only() -> None:
    data = _batch(
        candidate_nodes=[
            {
                "label": "prop:toy_statement",
                "kind": "proposition",
                "statement": "Toy proposition.",
                "proof": "",
                "remark": "",
                "source_note": "paper",
                "source_refs": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
            }
        ],
        verification_requests=[
            {"target": "def:zariski_open", "kind": "verify_definition"}
        ],
    )

    parsed = parse_learner_batch(json.dumps(data))

    assert parsed.candidate_nodes[0]["proof_status"] == "statement_only"
