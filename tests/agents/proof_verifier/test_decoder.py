"""Decoder tests for proof-verifier's three stages (issue #9)."""

from __future__ import annotations

import json

import pytest

from rethlas_kb_agents.proof_verifier.decoder import (
    DetailedVerdict,
    JudgeVerdict,
    ProofReviewParseError,
    StructuralVerdict,
    parse_detailed,
    parse_judge,
    parse_structural,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _judge_payload(**overrides) -> dict:
    p = {"difficulty": "hard", "rationale": "non-trivial"}
    p.update(overrides)
    return p


def _structural_payload(**overrides) -> dict:
    p = {
        "verdict": "pass",
        "rationale": "ok",
        "checks": [
            {"name": "statement_quality", "verdict": "pass"},
            {"name": "alignment", "verdict": "pass"},
            {"name": "completeness", "verdict": "pass"},
            {"name": "architecture", "verdict": "pass"},
        ],
    }
    p.update(overrides)
    return p


def _detailed_payload(**overrides) -> dict:
    p = {
        "verdict": "accepted",
        "rationale": "ok",
        "step_verdicts": [
            {"step_id": "1", "claim": "c", "verdict": "pass"},
        ],
    }
    p.update(overrides)
    return p


# ===========================================================================
# Judge
# ===========================================================================
def test_judge_hard_minimal() -> None:
    v = parse_judge(json.dumps(_judge_payload()))
    assert isinstance(v, JudgeVerdict)
    assert v.difficulty == "hard"
    assert v.verdict is None
    assert v.is_easy is False


def test_judge_easy_with_accepted() -> None:
    v = parse_judge(json.dumps(_judge_payload(
        difficulty="easy", verdict="accepted",
    )))
    assert v.difficulty == "easy"
    assert v.verdict == "accepted"
    assert v.is_easy


def test_judge_easy_gap_requires_gaps() -> None:
    with pytest.raises(ProofReviewParseError, match="easy_judge_gap_requires_gaps"):
        parse_judge(json.dumps(_judge_payload(
            difficulty="easy", verdict="gap",
        )))


def test_judge_easy_critical_requires_critical_errors() -> None:
    with pytest.raises(
        ProofReviewParseError,
        match="easy_judge_critical_requires_critical_errors",
    ):
        parse_judge(json.dumps(_judge_payload(
            difficulty="easy", verdict="critical",
        )))


def test_judge_easy_gap_with_gaps_passes() -> None:
    v = parse_judge(json.dumps(_judge_payload(
        difficulty="easy", verdict="gap", gaps=["step 3 unjustified"],
    )))
    assert v.verdict == "gap"
    assert v.gaps == ["step 3 unjustified"]


def test_judge_easy_requires_valid_verdict() -> None:
    with pytest.raises(
        ProofReviewParseError, match="easy_judge_requires_valid_verdict",
    ):
        parse_judge(json.dumps(_judge_payload(
            difficulty="easy", verdict="bogus",
        )))


def test_judge_invalid_difficulty() -> None:
    with pytest.raises(ProofReviewParseError, match="invalid_difficulty"):
        parse_judge(json.dumps(_judge_payload(difficulty="medium")))


def test_judge_missing_rationale() -> None:
    with pytest.raises(ProofReviewParseError, match="missing_rationale"):
        parse_judge(json.dumps({"difficulty": "hard", "rationale": ""}))


def test_judge_no_json_blob() -> None:
    with pytest.raises(ProofReviewParseError, match="no_judge_json"):
        parse_judge("only prose, no JSON")


def test_judge_hard_ignores_irrelevant_fields() -> None:
    v = parse_judge(json.dumps(_judge_payload(
        difficulty="hard",
        verdict="accepted",  # should be ignored
        gaps=["x"],          # should be ignored
        critical_errors=["y"],
    )))
    assert v.verdict is None
    assert v.gaps == []
    assert v.critical_errors == []


# ===========================================================================
# Structural
# ===========================================================================
def test_structural_pass_minimal() -> None:
    v = parse_structural(json.dumps(_structural_payload()))
    assert isinstance(v, StructuralVerdict)
    assert v.verdict == "pass"
    assert v.passed
    assert len(v.checks) == 4


def test_structural_fail_with_failing_check() -> None:
    payload = _structural_payload(verdict="fail")
    payload["checks"][2] = {
        "name": "completeness", "verdict": "fail",
        "notes": "case x=0 not handled",
    }
    v = parse_structural(json.dumps(payload))
    assert v.verdict == "fail"
    assert any(c.verdict == "fail" for c in v.checks)


def test_structural_fail_without_failing_check_rejected() -> None:
    """verdict=fail but every individual check passed → contradiction."""
    with pytest.raises(
        ProofReviewParseError, match="structural_fail_requires_failed_check",
    ):
        parse_structural(json.dumps(_structural_payload(verdict="fail")))


def test_structural_invalid_check_name() -> None:
    payload = _structural_payload()
    payload["checks"][0]["name"] = "made_up_check"
    with pytest.raises(
        ProofReviewParseError, match="invalid_structural_check_name",
    ):
        parse_structural(json.dumps(payload))


def test_structural_duplicate_check_name_rejected() -> None:
    payload = _structural_payload()
    payload["checks"].append(
        {"name": "alignment", "verdict": "pass"},
    )
    with pytest.raises(
        ProofReviewParseError, match="duplicate_structural_check",
    ):
        parse_structural(json.dumps(payload))


def test_structural_invalid_overall_verdict() -> None:
    with pytest.raises(ProofReviewParseError, match="invalid_structural_verdict"):
        parse_structural(json.dumps(_structural_payload(verdict="maybe")))


def test_structural_checks_not_list() -> None:
    payload = _structural_payload()
    payload["checks"] = "not a list"
    with pytest.raises(ProofReviewParseError, match="structural_checks_not_list"):
        parse_structural(json.dumps(payload))


# ===========================================================================
# Detailed
# ===========================================================================
def test_detailed_accepted_minimal() -> None:
    v = parse_detailed(json.dumps(_detailed_payload()))
    assert isinstance(v, DetailedVerdict)
    assert v.is_accepted
    assert len(v.step_verdicts) == 1


def test_detailed_gap_requires_failed_step_or_fatal_issue() -> None:
    """verdict=gap with all-pass steps and no fatal issue → contradiction."""
    with pytest.raises(
        ProofReviewParseError, match="detailed_verdict_requires_evidence",
    ):
        parse_detailed(json.dumps(_detailed_payload(verdict="gap")))


def test_detailed_gap_with_failed_step_passes() -> None:
    v = parse_detailed(json.dumps(_detailed_payload(
        verdict="gap",
        step_verdicts=[
            {"step_id": "1", "claim": "c1", "verdict": "pass"},
            {"step_id": "2", "claim": "c2", "verdict": "fail", "notes": "no justification"},
        ],
    )))
    assert v.verdict == "gap"


def test_detailed_critical_with_fatal_rigor_issue_passes() -> None:
    v = parse_detailed(json.dumps(_detailed_payload(
        verdict="critical",
        rigor_issues=[
            {"severity": "fatal", "claim": "step 5", "notes": "WLOG is invalid"},
        ],
    )))
    assert v.verdict == "critical"
    assert len(v.rigor_issues) == 1


def test_detailed_uncertain_with_uncertain_step_passes() -> None:
    v = parse_detailed(json.dumps(_detailed_payload(
        verdict="uncertain",
        step_verdicts=[
            {"step_id": "1", "claim": "c", "verdict": "uncertain"},
        ],
    )))
    assert v.verdict == "uncertain"


def test_detailed_invalid_step_verdict() -> None:
    payload = _detailed_payload()
    payload["step_verdicts"][0]["verdict"] = "maybe"
    with pytest.raises(ProofReviewParseError, match="invalid_step_verdict"):
        parse_detailed(json.dumps(payload))


def test_detailed_step_missing_step_id() -> None:
    payload = _detailed_payload()
    payload["step_verdicts"][0]["step_id"] = ""
    with pytest.raises(
        ProofReviewParseError, match="step_verdict_missing_step_id",
    ):
        parse_detailed(json.dumps(payload))


def test_detailed_invalid_rigor_severity() -> None:
    payload = _detailed_payload(
        rigor_issues=[{"severity": "minor-ish", "claim": "x"}],
    )
    with pytest.raises(ProofReviewParseError, match="invalid_rigor_severity"):
        parse_detailed(json.dumps(payload))


def test_detailed_invalid_top_level_verdict() -> None:
    with pytest.raises(ProofReviewParseError, match="invalid_detailed_verdict"):
        parse_detailed(json.dumps(_detailed_payload(verdict="bogus")))


# ===========================================================================
# Cross-cutting: raw is preserved on exceptions
# ===========================================================================
def test_judge_exception_carries_raw() -> None:
    raw = json.dumps(_judge_payload(difficulty="bad"))
    try:
        parse_judge(raw)
    except ProofReviewParseError as exc:
        assert exc.raw == raw
    else:
        pytest.fail("expected ProofReviewParseError")


def test_structural_exception_carries_raw() -> None:
    payload = _structural_payload(verdict="fail")  # contradiction
    raw = json.dumps(payload)
    try:
        parse_structural(raw)
    except ProofReviewParseError as exc:
        assert exc.raw == raw
    else:
        pytest.fail("expected ProofReviewParseError")


def test_detailed_exception_carries_raw() -> None:
    raw = json.dumps(_detailed_payload(verdict="bogus"))
    try:
        parse_detailed(raw)
    except ProofReviewParseError as exc:
        assert exc.raw == raw
    else:
        pytest.fail("expected ProofReviewParseError")
