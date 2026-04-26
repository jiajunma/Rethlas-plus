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
