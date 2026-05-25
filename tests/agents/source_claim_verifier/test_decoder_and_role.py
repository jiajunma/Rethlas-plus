"""Source-claim-verifier decoder + role tests (issue #12)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import MockBackend
from rethlas_kb_agents.source_claim_verifier import (
    SourceClaimReview,
    SourceClaimReviewParseError,
    SourceClaimVerifier,
    parse,
)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------
def _payload(**overrides):
    p = {
        "decision": "accepted",
        "rationale": "node statement matches source",
        "quoted_node_statement": "for every group G, ...",
        "quoted_source_statement": "for every group G, ...",
    }
    p.update(overrides)
    return p


def test_accepted_minimal_round_trips() -> None:
    r = parse(json.dumps(_payload()))
    assert isinstance(r, SourceClaimReview)
    assert r.is_accepted
    assert r.quoted_node_statement.startswith("for every")


def test_mismatch_requires_differences() -> None:
    with pytest.raises(
        SourceClaimReviewParseError, match="mismatch_requires_differences",
    ):
        parse(json.dumps(_payload(decision="mismatch")))


def test_mismatch_with_differences_round_trips() -> None:
    r = parse(json.dumps(_payload(
        decision="mismatch",
        differences=[
            "node says 'for all', source says 'there exists'",
            "node uses Z/p, source uses Q",
        ],
    )))
    assert r.decision == "mismatch"
    assert len(r.differences) == 2


def test_proof_gap_requires_proof_issues() -> None:
    with pytest.raises(
        SourceClaimReviewParseError, match="proof_gap_requires_proof_issues",
    ):
        parse(json.dumps(_payload(decision="proof_gap")))


def test_proof_critical_requires_proof_issues() -> None:
    with pytest.raises(
        SourceClaimReviewParseError,
        match="proof_critical_requires_proof_issues",
    ):
        parse(json.dumps(_payload(decision="proof_critical")))


def test_proof_gap_with_issues_round_trips() -> None:
    r = parse(json.dumps(_payload(
        decision="proof_gap",
        proof_issues=["step 3 uses WLOG without justification"],
    )))
    assert r.decision == "proof_gap"
    assert "WLOG" in r.proof_issues[0]


def test_cannot_verify_requires_missing_evidence() -> None:
    with pytest.raises(
        SourceClaimReviewParseError,
        match="cannot_verify_requires_missing_evidence",
    ):
        parse(json.dumps(_payload(decision="cannot_verify")))


def test_cannot_verify_with_missing_evidence_round_trips() -> None:
    r = parse(json.dumps(_payload(
        decision="cannot_verify",
        missing_evidence="source passage only includes the statement, not the proof",
    )))
    assert r.decision == "cannot_verify"
    assert "source passage" in r.missing_evidence


def test_invalid_decision_rejected() -> None:
    with pytest.raises(SourceClaimReviewParseError, match="invalid_decision"):
        parse(json.dumps(_payload(decision="bogus")))


def test_missing_rationale_rejected() -> None:
    with pytest.raises(SourceClaimReviewParseError, match="missing_rationale"):
        parse(json.dumps({"decision": "accepted", "rationale": ""}))


def test_quoted_statements_default_to_empty() -> None:
    r = parse(json.dumps({"decision": "accepted", "rationale": "x"}))
    assert r.quoted_node_statement == ""
    assert r.quoted_source_statement == ""


def test_quoted_statement_non_string_rejected() -> None:
    p = _payload(); p["quoted_node_statement"] = ["a", "b"]
    with pytest.raises(
        SourceClaimReviewParseError, match="quoted_node_statement_not_string",
    ):
        parse(json.dumps(p))


def test_exception_carries_raw() -> None:
    raw = json.dumps(_payload(decision="bogus"))
    try:
        parse(raw)
    except SourceClaimReviewParseError as exc:
        assert exc.raw == raw
    else:
        pytest.fail("expected exception")


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------
EXT_THM_MD = textwrap.dedent("""\
    ---
    id: references.lagrange_external
    title: Lagrange's Theorem (external — cited from Lang's Algebra)
    kind: external-theorem
    status: staged
    primary_topic: references
    topics: [references]
    lean:
      modules: [Mathlib.GroupTheory.Lagrange]
      declarations: [Lagrange]
    source:
      spans:
        - artifact: lang-algebra
          locator: "Chapter I, Proposition 2.1"
          format: section
    ---

    # Lagrange's Theorem

    > **Theorem (Lang, I.2.1).** Let G be a finite group and H a subgroup.
    > Then |H| divides |G|.
    """)


@pytest.fixture
def adapter(tmp_path: Path) -> KbAdapter:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "staged" / "references").mkdir(parents=True)
    (knowledge / "staged" / "references" / "lagrange.md").write_text(EXT_THM_MD)
    return KbAdapter(tmp_path)


def test_role_runs_accepted_with_source_passage(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted",
        "rationale": "node + source match exactly",
        "quoted_node_statement": "Let G be a finite group and H a subgroup. Then |H| divides |G|.",
        "quoted_source_statement": "Let G be a finite group and H a subgroup. Then |H| divides |G|.",
    }))
    auditor = SourceClaimVerifier(backend=backend)
    r = auditor.run(
        "references.lagrange_external", adapter,
        source_passage=(
            "Lang, Algebra, Proposition I.2.1: For a finite group G and a "
            "subgroup H, |H| divides |G|."
        ),
    )
    assert r.is_accepted
    assert backend.last_call["agent_role"] == "source-claim-verifier"
    # Source passage made it into the prompt
    assert "Lang, Algebra" in backend.last_call["prompt"]


def test_role_omits_source_section_when_no_passage(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "cannot_verify",
        "rationale": "no source passage given",
        "missing_evidence": "need extract from Lang, Chapter I",
    }))
    auditor = SourceClaimVerifier(backend=backend)
    r = auditor.run("references.lagrange_external", adapter)
    assert r.decision == "cannot_verify"
    assert "No pre-extracted source passage" in backend.last_call["prompt"]


def test_role_includes_source_proof_section_when_provided(
    adapter: KbAdapter,
) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted",
        "rationale": "alignment + proof OK",
        "quoted_node_statement": "...",
        "quoted_source_statement": "...",
    }))
    auditor = SourceClaimVerifier(backend=backend)
    auditor.run(
        "references.lagrange_external", adapter,
        source_passage="source theorem text",
        source_proof="**Proof.** Coset equivalence relation ...",
    )
    prompt = backend.last_call["prompt"]
    assert "Source proof" in prompt
    assert "Coset equivalence" in prompt


def test_role_picks_up_project_rules(adapter: KbAdapter) -> None:
    adapter.rules_dir.mkdir(parents=True, exist_ok=True)
    (adapter.rules_dir / "source-claim-verifier.md").write_text(
        "- Always check Mathlib declarations referenced in the lean field.\n"
    )
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok",
        "quoted_node_statement": "x", "quoted_source_statement": "x",
    }))
    auditor = SourceClaimVerifier(backend=backend)
    auditor.run("references.lagrange_external", adapter)
    assert "Mathlib declarations" in backend.last_call["prompt"]


def test_role_surfaces_parse_error(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response="not json")
    auditor = SourceClaimVerifier(backend=backend)
    with pytest.raises(SourceClaimReviewParseError):
        auditor.run("references.lagrange_external", adapter)
