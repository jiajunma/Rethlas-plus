"""Decoder tests for proof-gap-filler (issue #10)."""

from __future__ import annotations

import json

import pytest

from rethlas_kb_agents.proof_gap_filler.decoder import (
    GapFillReview,
    GapFillReviewParseError,
    NewSubLemma,
    parse,
)


def _payload(**overrides):
    p = {"decision": "filled", "rationale": "looks good",
         "filled_proof": "**Proof.** by direct construction. $\\square$"}
    p.update(overrides)
    return p


def test_filled_minimal_round_trips() -> None:
    r = parse(json.dumps(_payload()))
    assert isinstance(r, GapFillReview)
    assert r.is_filled
    assert r.filled_proof.startswith("**Proof.**")
    assert r.writes_proof


def test_filled_without_filled_proof_rejected() -> None:
    p = _payload(); p["filled_proof"] = ""
    with pytest.raises(GapFillReviewParseError, match="filled_requires_filled_proof"):
        parse(json.dumps(p))


def test_partial_requires_gap_remaining() -> None:
    p = _payload(decision="partial")  # gap_remaining missing
    with pytest.raises(GapFillReviewParseError, match="partial_requires_gap_remaining"):
        parse(json.dumps(p))


def test_partial_with_gap_remaining_and_filled_proof() -> None:
    r = parse(json.dumps(_payload(
        decision="partial",
        filled_proof="partial proof draft",
        gap_remaining="step 3 unproven",
    )))
    assert r.decision == "partial"
    assert r.writes_proof  # partial still writes its draft
    assert "step 3" in r.gap_remaining


def test_cannot_fill_requires_gap_remaining() -> None:
    with pytest.raises(GapFillReviewParseError, match="cannot_fill_requires_gap_remaining"):
        parse(json.dumps(_payload(decision="cannot_fill", filled_proof="")))


def test_cannot_fill_with_gap_does_not_write_proof() -> None:
    r = parse(json.dumps(_payload(
        decision="cannot_fill",
        filled_proof="",
        gap_remaining="counterexample: G = trivial group",
    )))
    assert r.decision == "cannot_fill"
    assert not r.writes_proof
    assert "counterexample" in r.gap_remaining


def test_invalid_decision_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="invalid_decision"):
        parse(json.dumps(_payload(decision="maybe")))


def test_missing_rationale_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="missing_rationale"):
        parse(json.dumps({"decision": "filled", "rationale": ""}))


def test_no_json_blob_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="no_review_json"):
        parse("only prose, no JSON here")


def test_new_sublemmas_round_trip() -> None:
    r = parse(json.dumps(_payload(
        new_sublemmas=[
            {"id": "algebra.helper", "statement": "for all g in G, g^|G| = e",
             "rationale": "needed for case n=p"},
            {"id": "algebra.helper2", "statement": "G/H is well-defined for normal H"},
        ],
    )))
    assert len(r.new_sublemmas) == 2
    assert all(isinstance(s, NewSubLemma) for s in r.new_sublemmas)
    assert r.new_sublemmas[0].id == "algebra.helper"
    assert r.new_sublemmas[1].rationale == ""  # missing → empty


def test_sublemma_missing_id_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="sublemma_missing_id"):
        parse(json.dumps(_payload(
            new_sublemmas=[{"id": "", "statement": "x"}],
        )))


def test_sublemma_missing_statement_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="sublemma_missing_statement"):
        parse(json.dumps(_payload(
            new_sublemmas=[{"id": "x.y", "statement": ""}],
        )))


def test_sublemma_not_object_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="sublemma_not_object"):
        parse(json.dumps(_payload(new_sublemmas=["just a string"])))


def test_new_sublemmas_not_list_rejected() -> None:
    with pytest.raises(GapFillReviewParseError, match="new_sublemmas_not_list"):
        parse(json.dumps(_payload(new_sublemmas="not a list")))


def test_suggested_approaches_list_round_trips() -> None:
    r = parse(json.dumps(_payload(
        suggested_approaches=["induction on n", "contradiction"],
    )))
    assert r.suggested_approaches == ["induction on n", "contradiction"]


def test_exception_carries_raw() -> None:
    raw = json.dumps(_payload(decision="bogus"))
    try:
        parse(raw)
    except GapFillReviewParseError as exc:
        assert exc.raw == raw
    else:
        pytest.fail("expected GapFillReviewParseError")
