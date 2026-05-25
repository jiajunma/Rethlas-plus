"""Counterexample-hunter decoder + role tests (issue #11)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import MockBackend
from rethlas_kb_agents.counterexample_hunter import (
    CounterexampleHunter,
    CounterexampleHuntReview,
    CounterexampleHuntReviewParseError,
    Witness,
    parse,
)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------
def _payload(**overrides):
    p = {
        "decision": "no_counterexample_found",
        "rationale": "all cases satisfy",
        "attempted_cases": [{"description": "G cyclic order 3", "outcome": "satisfies"}],
    }
    p.update(overrides)
    return p


def test_no_counterexample_with_cases_round_trips() -> None:
    r = parse(json.dumps(_payload()))
    assert isinstance(r, CounterexampleHuntReview)
    assert r.decision == "no_counterexample_found"
    assert not r.refuted
    assert len(r.attempted_cases) == 1


def test_no_counterexample_without_cases_rejected() -> None:
    p = _payload(); p["attempted_cases"] = []
    with pytest.raises(
        CounterexampleHuntReviewParseError,
        match="no_counterexample_requires_attempted_cases",
    ):
        parse(json.dumps(p))


def test_counterexample_found_with_witness_round_trips() -> None:
    r = parse(json.dumps({
        "decision": "counterexample_found",
        "rationale": "found a small witness",
        "witness": {
            "description": "G = Z/4, H = {0, 2}, claim fails for [3]",
            "instantiation": "Python: G = Z/4; H = {0,2}; [3] not in H",
            "verification": "computed in SymPy; |H| = 2 does not divide [3]'s order",
        },
        "suggested_fixes": ["add 'H normal'", "weaken conclusion to 'divides or equals'"],
    }))
    assert r.refuted
    assert isinstance(r.witness, Witness)
    assert "Z/4" in r.witness.description
    assert len(r.suggested_fixes) == 2


def test_counterexample_found_without_witness_rejected() -> None:
    with pytest.raises(
        CounterexampleHuntReviewParseError,
        match="counterexample_found_requires_witness",
    ):
        parse(json.dumps({
            "decision": "counterexample_found",
            "rationale": "claim is false",
            # no witness
        }))


def test_inconclusive_requires_why() -> None:
    with pytest.raises(
        CounterexampleHuntReviewParseError,
        match="inconclusive_requires_why",
    ):
        parse(json.dumps({
            "decision": "inconclusive",
            "rationale": "ran out of ideas",
        }))


def test_inconclusive_with_why_round_trips() -> None:
    r = parse(json.dumps({
        "decision": "inconclusive",
        "rationale": "case explosion",
        "why_inconclusive": "n=20 alone takes 10^6 cases; need a smarter strategy",
    }))
    assert r.decision == "inconclusive"
    assert "case explosion" in r.rationale
    assert "10^6" in r.why_inconclusive


def test_invalid_decision_rejected() -> None:
    with pytest.raises(CounterexampleHuntReviewParseError, match="invalid_decision"):
        parse(json.dumps(_payload(decision="found_maybe")))


def test_missing_rationale_rejected() -> None:
    with pytest.raises(CounterexampleHuntReviewParseError, match="missing_rationale"):
        parse(json.dumps({
            "decision": "no_counterexample_found",
            "rationale": "",
            "attempted_cases": [{"description": "x", "outcome": "y"}],
        }))


def test_witness_missing_description_rejected() -> None:
    with pytest.raises(
        CounterexampleHuntReviewParseError, match="witness_missing_description",
    ):
        parse(json.dumps({
            "decision": "counterexample_found", "rationale": "found",
            "witness": {"description": ""},
        }))


def test_attempted_case_missing_description_rejected() -> None:
    with pytest.raises(
        CounterexampleHuntReviewParseError,
        match="attempted_case_missing_description",
    ):
        parse(json.dumps(_payload(
            attempted_cases=[{"description": "", "outcome": "x"}],
        )))


def test_attempted_case_not_object_rejected() -> None:
    with pytest.raises(
        CounterexampleHuntReviewParseError,
        match="attempted_case_not_object",
    ):
        parse(json.dumps(_payload(attempted_cases=["string instead of object"])))


def test_witness_not_object_rejected() -> None:
    with pytest.raises(CounterexampleHuntReviewParseError, match="witness_not_object"):
        parse(json.dumps({
            "decision": "counterexample_found", "rationale": "x",
            "witness": "should be an object",
        }))


def test_exception_carries_raw() -> None:
    raw = json.dumps(_payload(decision="bogus"))
    try:
        parse(raw)
    except CounterexampleHuntReviewParseError as exc:
        assert exc.raw == raw
    else:
        pytest.fail("expected exception")


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------
NODE_MD = textwrap.dedent("""\
    ---
    id: algebra.bogus_claim
    title: Every finite group is cyclic
    kind: theorem
    status: staged
    primary_topic: algebra
    topics: [algebra]
    ---

    # Bogus claim

    > **Theorem.** Every finite group is cyclic.

    **Proof.** TODO
    """)


@pytest.fixture
def adapter(tmp_path: Path) -> KbAdapter:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra" / "bogus.md").write_text(NODE_MD)
    return KbAdapter(tmp_path)


def test_role_runs_with_witness(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "counterexample_found",
        "rationale": "S_3 is finite non-cyclic",
        "witness": {
            "description": "S_3 has 6 elements but is non-abelian",
            "instantiation": "S_3 = symmetric group on 3 elements",
            "verification": "|S_3| = 6, but S_3 has no element of order 6",
        },
        "suggested_fixes": [
            "qualify as 'every finite abelian group of prime power order'",
        ],
    }))
    hunter = CounterexampleHunter(backend=backend)
    r = hunter.run("algebra.bogus_claim", adapter)
    assert r.refuted
    assert r.witness is not None
    assert "S_3" in r.witness.description
    assert backend.last_call["agent_role"] == "counterexample-hunter"


def test_role_picks_up_project_rules(adapter: KbAdapter) -> None:
    adapter.rules_dir.mkdir(parents=True, exist_ok=True)
    (adapter.rules_dir / "counterexample-hunter.md").write_text(
        "- Always include topology examples for analysis nodes.\n"
    )
    backend = MockBackend(canned_response=json.dumps({
        "decision": "counterexample_found",
        "rationale": "found one",
        "witness": {"description": "x"},
    }))
    hunter = CounterexampleHunter(backend=backend)
    hunter.run("algebra.bogus_claim", adapter)
    assert "topology examples" in backend.last_call["prompt"]


def test_role_surfaces_parse_error(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response="not json at all")
    hunter = CounterexampleHunter(backend=backend)
    with pytest.raises(CounterexampleHuntReviewParseError):
        hunter.run("algebra.bogus_claim", adapter)
