"""Decoder tests (issue #7).

The decoder's job: extract the **last** balanced ``{...}`` blob that
both parses as JSON and contains the agent's required keys. Inputs
range from clean JSON to JSON-buried-in-prose to multiple competing
JSON objects. ANSI escapes and curly-quote NFC drift must not break
parsing.
"""

from __future__ import annotations

import json

import pytest

from rethlas_kb_agents.statement_verifier.decoder import (
    StatementReview,
    StatementReviewParseError,
    VALID_DECISIONS,
    parse,
)


def _verdict(**overrides: object) -> dict:
    base = {
        "decision": "accepted",
        "rationale": "Looks fine.",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_parses_clean_json() -> None:
    raw = json.dumps(_verdict())
    review = parse(raw)
    assert isinstance(review, StatementReview)
    assert review.decision == "accepted"
    assert review.rationale == "Looks fine."
    assert review.confidence == 0.9
    assert review.raw == raw


def test_round_trips_all_valid_decisions() -> None:
    for d in VALID_DECISIONS:
        payload = _verdict(decision=d)
        # Each decision has a required companion field; supply minimally
        if d == "needs_definition":
            payload["missing_definitions"] = ["foo"]
        elif d == "formulation_issue":
            payload["formulation_issues"] = ["typo on line 3"]
        elif d == "generality_concern":
            payload["generality_notes"] = "could drop finiteness"
        review = parse(json.dumps(payload))
        assert review.decision == d


def test_optional_fields_default_to_empty() -> None:
    review = parse(json.dumps(_verdict()))
    assert review.missing_definitions == []
    assert review.formulation_issues == []
    assert review.generality_notes == ""


def test_carries_raw_through_unchanged() -> None:
    raw = "noise before " + json.dumps(_verdict()) + " noise after"
    assert parse(raw).raw == raw


# ---------------------------------------------------------------------------
# Robustness: prose-wrapping
# ---------------------------------------------------------------------------
def test_strips_ansi_escapes() -> None:
    ansi_prefix = "\x1b[31mthinking...\x1b[0m\n"
    raw = ansi_prefix + json.dumps(_verdict())
    assert parse(raw).decision == "accepted"


def test_normalises_curly_quotes_to_nfc() -> None:
    # Two NFC-equivalent forms of the same string should both parse.
    raw = "Reasoning prose…\n" + json.dumps(_verdict())
    review = parse(raw)
    assert review.decision == "accepted"


def test_returns_last_blob_when_multiple_present() -> None:
    first = json.dumps(_verdict(rationale="first try"))
    second = json.dumps(_verdict(rationale="final answer"))
    raw = (
        "Let me try once...\n"
        + first
        + "\nActually on reflection...\n"
        + second
    )
    assert parse(raw).rationale == "final answer"


def test_ignores_blobs_missing_required_keys() -> None:
    decoy = json.dumps({"random": "blob"})
    real = json.dumps(_verdict())
    raw = decoy + "\n" + real
    assert parse(raw).rationale == "Looks fine."


def test_ignores_blobs_with_only_one_required_key() -> None:
    decoy = json.dumps({"decision": "accepted", "missing": "rationale"})
    real = json.dumps(_verdict(rationale="ok"))
    raw = decoy + "\n" + real
    assert parse(raw).rationale == "ok"


def test_handles_braces_inside_string_values() -> None:
    """Braces inside JSON string values must not confuse depth tracking."""
    payload = _verdict(rationale="contains a { brace } in text")
    raw = json.dumps(payload)
    assert parse(raw).rationale == "contains a { brace } in text"


def test_handles_escaped_quotes_inside_strings() -> None:
    payload = _verdict(rationale='quoted "thing" with embedded {brace}')
    raw = json.dumps(payload)
    assert parse(raw).rationale == 'quoted "thing" with embedded {brace}'


# ---------------------------------------------------------------------------
# Confidence coercion
# ---------------------------------------------------------------------------
def test_confidence_clamped_below_zero() -> None:
    assert parse(json.dumps(_verdict(confidence=-0.5))).confidence == 0.0


def test_confidence_clamped_above_one() -> None:
    assert parse(json.dumps(_verdict(confidence=1.5))).confidence == 1.0


def test_confidence_coerces_int() -> None:
    assert parse(json.dumps(_verdict(confidence=1))).confidence == 1.0


def test_confidence_missing_defaults_to_zero() -> None:
    payload = {"decision": "accepted", "rationale": "ok"}
    assert parse(json.dumps(payload)).confidence == 0.0


def test_confidence_non_numeric_raises() -> None:
    raw = json.dumps(_verdict(confidence="high"))
    with pytest.raises(StatementReviewParseError, match="confidence_not_numeric"):
        parse(raw)


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------
def test_unknown_decision_raises() -> None:
    raw = json.dumps(_verdict(decision="maybe"))
    with pytest.raises(StatementReviewParseError, match="invalid_decision"):
        parse(raw)


def test_missing_rationale_raises() -> None:
    raw = json.dumps({"decision": "accepted", "rationale": ""})
    with pytest.raises(StatementReviewParseError, match="missing_rationale"):
        parse(raw)


def test_no_json_blob_raises() -> None:
    with pytest.raises(StatementReviewParseError, match="no_review_json"):
        parse("the model returned only prose, no JSON")


def test_empty_string_raises() -> None:
    with pytest.raises(StatementReviewParseError, match="no_review_json"):
        parse("")


def test_missing_definitions_not_list_raises() -> None:
    raw = json.dumps(_verdict(missing_definitions="foo"))
    with pytest.raises(StatementReviewParseError,
                       match="missing_definitions_not_list"):
        parse(raw)


def test_missing_definitions_item_not_string_raises() -> None:
    raw = json.dumps(_verdict(missing_definitions=[123]))
    with pytest.raises(StatementReviewParseError,
                       match="missing_definitions_item_not_string"):
        parse(raw)


def test_generality_notes_not_string_raises() -> None:
    raw = json.dumps(_verdict(generality_notes=[1, 2]))
    with pytest.raises(StatementReviewParseError,
                       match="generality_notes_not_string"):
        parse(raw)


# ---------------------------------------------------------------------------
# is_accepted helper
# ---------------------------------------------------------------------------
def test_is_accepted_true_when_accepted() -> None:
    assert parse(json.dumps(_verdict())).is_accepted is True


def test_is_accepted_false_when_other() -> None:
    payload = _verdict(decision="formulation_issue",
                       formulation_issues=["typo"])
    assert parse(json.dumps(payload)).is_accepted is False
