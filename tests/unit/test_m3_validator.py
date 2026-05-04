"""M3 — admission-side validator rules."""

from __future__ import annotations

import pytest

from librarian.validator import AdmissionError, validate_admission


def _body(*, label: str, kind: str, statement: str = "S", proof: str = "") -> dict:
    payload = {
        "kind": kind,
        "statement": statement,
        "remark": "",
        "source_note": "",
    }
    if proof:
        payload["proof"] = proof
    return {
        "event_id": "20260425T120000.000-0001-aaaaaaaaaaaaaaaa",
        "type": "user.node_added",
        "actor": "user:alice",
        "ts": "2026-04-25T12:00:00.000+08:00",
        "target": label,
        "payload": payload,
    }


def test_external_theorem_requires_non_empty_source_note() -> None:
    with pytest.raises(AdmissionError, match="source_note"):
        validate_admission(
            _body(label="ext:riemann", kind="external_theorem")
        )


def test_placeholder_label_rejected() -> None:
    with pytest.raises(AdmissionError, match="placeholder"):
        validate_admission(
            _body(label="thm:main", kind="theorem", proof="p")
        )


def test_additional_placeholder_examples_rejected() -> None:
    for label, kind in (
        ("def:object", "definition"),
        ("prop:claim1", "proposition"),
        ("lem:key_step", "lemma"),
    ):
        with pytest.raises(AdmissionError, match="placeholder"):
            validate_admission(
                _body(label=label, kind=kind, proof="" if kind == "definition" else "p")
            )


def test_uppercase_slug_rejected() -> None:
    with pytest.raises(AdmissionError, match="invalid slug"):
        validate_admission(
            _body(label="thm:Main_Result", kind="theorem", proof="p")
        )


def test_phase3_learner_batch_admission_rejects_missing_span_hash() -> None:
    body = {
        "event_id": "20260425T120000.000-0001-aaaaaaaaaaaaaaaa",
        "type": "learner.batch_proposed",
        "actor": "learner:alice",
        "ts": "2026-04-25T12:00:00.000+08:00",
        "payload": {
            "source_id": "src:toy",
            "learner_run": "learn_toy_001",
            "source_spans": [{"span_id": "span:toy:1", "span_hash": "sha256:x"}],
            "candidate_nodes": [
                {
                    "label": "def:toy",
                    "kind": "definition",
                    "statement": "Toy.",
                    "source_refs": [{"span_id": "span:toy:1"}],
                }
            ],
            "verification_requests": [{"target": "def:toy", "kind": "verify_definition"}],
        },
    }
    with pytest.raises(AdmissionError, match="missing_source_span_hash"):
        validate_admission(body)


def test_phase3_referee_requested_detail_blocks_acceptance() -> None:
    report = {
        "output_schema": "referee_report_v1",
        "review_id": "review_toy_001",
        "target": "thm:toy",
        "workspace_path": "reviews/review_toy_001",
        "target_hashes": {},
        "verdict": "accepted",
        "requested_details": [
            {"requested_detail": "State the missing lemma.", "blocks_verdict": True}
        ],
    }
    body = {
        "event_id": "20260425T120000.000-0001-aaaaaaaaaaaaaaaa",
        "type": "referee.review_completed",
        "actor": "referee:alice",
        "ts": "2026-04-25T12:00:00.000+08:00",
        "target": "thm:toy",
        "payload": {"report": report},
    }
    with pytest.raises(AdmissionError, match="requested_detail_blocks_acceptance"):
        validate_admission(body)
