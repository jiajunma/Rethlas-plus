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
                "extraction_kind": "implicit_paragraph",
                "source_locator": "Section 1, prose paragraph",
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
    assert parsed.candidate_nodes[0]["extraction_kind"] == "implicit_paragraph"
    assert parsed.candidate_nodes[0]["source_locator"] == "Section 1, prose paragraph"
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


def test_learner_definition_verification_accepts_for_label() -> None:
    data = _batch(
        verification_requests=[
            {"for_label": "def:zariski_open", "kind": "verify_definition"}
        ]
    )

    parsed = parse_learner_batch(json.dumps(data))

    assert parsed.verification_requests[0]["for_label"] == "def:zariski_open"


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


def test_learner_candidate_accepts_type_alias_for_kind() -> None:
    data = _batch()
    data["candidate_nodes"][0]["type"] = data["candidate_nodes"][0].pop("kind")

    parsed = parse_learner_batch(json.dumps(data))

    assert parsed.candidate_nodes[0]["kind"] == "definition"


def test_strict_statement_only_accepts_for_label_bridge() -> None:
    contract = {
        "proof_capture": "strict",
        "notation_normalization": "canonical",
        "statement_only_policy": "requires_statement_only_reason_and_bridge_or_issue",
    }
    data = _batch(
        learning_contract=contract,
        notation_contexts=[
            {
                "context_id": "ctx:toy",
                "canonical_symbols": [{"symbol": "X", "meaning": "toy space"}],
            }
        ],
        candidate_nodes=[
            {
                "label": "thm:toy_statement",
                "kind": "theorem",
                "statement": "Toy theorem.",
                "proof": "Only the statement is present in the supplied span.",
                "proof_status": "statement_only",
                "proof_steps": [
                    {
                        "step": "Record the theorem statement.",
                        "source_ref": {
                            "span_id": "span:toy:1",
                            "span_hash": "sha256:" + "1" * 64,
                        },
                    }
                ],
                "statement_only_reason": "The dispatched source span contains no proof.",
                "source_refs": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
            }
        ],
        bridge_requests=[
            {
                "kind": "proof_bridge",
                "for_label": "thm:toy_statement",
                "reason": "Need the later proof span.",
            }
        ],
        verification_requests=[
            {"target": "def:zariski_open", "kind": "verify_definition"}
        ],
    )

    parsed = parse_learner_batch(
        json.dumps(data), expected_learning_contract=contract
    )

    assert parsed.candidate_nodes[0]["proof_status"] == "statement_only"


def test_strict_proof_step_requires_source_ref() -> None:
    contract = {
        "proof_capture": "strict",
        "notation_normalization": "canonical",
        "statement_only_policy": "requires_statement_only_reason_and_bridge_or_issue",
    }
    data = _batch(
        learning_contract=contract,
        notation_contexts=[
            {
                "context_id": "ctx:toy",
                "canonical_symbols": [{"symbol": "X", "meaning": "toy space"}],
            }
        ],
        candidate_nodes=[
            {
                "label": "lem:toy_step",
                "kind": "lemma",
                "statement": "Toy step.",
                "proof": "Apply the definition.",
                "proof_status": "proof_sketch_extracted",
                "proof_steps": [{"step": "Apply the definition."}],
                "source_refs": [
                    {"span_id": "span:toy:1", "span_hash": "sha256:" + "1" * 64}
                ],
            }
        ],
        verification_requests=[
            {"target": "def:zariski_open", "kind": "verify_definition"}
        ],
    )

    with pytest.raises(LearnerDecodeError, match="proof_steps\\[\\] requires source_ref"):
        parse_learner_batch(json.dumps(data), expected_learning_contract=contract)
