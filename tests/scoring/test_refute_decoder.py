"""refute.prompt + refute.decoder — S4-light primitives."""

from __future__ import annotations

import pytest

from refute.decoder import RefuteParseError, parse_refute_verdict
from refute.prompt import REFUTE_PROMPT, compose_prompt
from rethlas_scoring.refute import RefuteVerdictKind


# ---------------------------------------------------------------------------
# compose_prompt
# ---------------------------------------------------------------------------
def test_compose_prompt_embeds_claim_text() -> None:
    p = compose_prompt("for all n, n^2 >= 0")
    assert "for all n, n^2 >= 0" in p


def test_compose_prompt_strips_whitespace() -> None:
    p = compose_prompt("  whitespace claim   ")
    assert "  whitespace claim   " not in p
    assert "whitespace claim" in p


def test_compose_prompt_uses_refute_template() -> None:
    p = compose_prompt("x")
    assert "counterexample" in p
    assert '"severity"' in p
    assert "{claim}" in REFUTE_PROMPT


# ---------------------------------------------------------------------------
# parse_refute_verdict — happy paths
# ---------------------------------------------------------------------------
def test_parse_no_issue_minimal() -> None:
    v = parse_refute_verdict(
        '{"verdict": "no_issue", "details": "looks fine", "severity": 0.0}'
    )
    assert v.verdict_kind is RefuteVerdictKind.NO_ISSUE
    assert v.severity == 0.0
    assert v.details == "looks fine"
    assert not v.found_counterexample


def test_parse_counterexample_marks_found() -> None:
    v = parse_refute_verdict(
        '{"verdict": "counterexample", "details": "x=0", "severity": 0.9}'
    )
    assert v.verdict_kind is RefuteVerdictKind.COUNTEREXAMPLE
    assert v.found_counterexample
    assert v.severity == 0.9


def test_parse_fragile_and_hidden_assumption_kinds() -> None:
    v_fragile = parse_refute_verdict(
        '{"verdict": "fragile", "details": "edge case at n=1", "severity": 0.4}'
    )
    assert v_fragile.verdict_kind is RefuteVerdictKind.FRAGILE

    v_hidden = parse_refute_verdict(
        '{"verdict": "hidden_assumption", "details": "assumes T2", "severity": 0.6}'
    )
    assert v_hidden.verdict_kind is RefuteVerdictKind.HIDDEN_ASSUMPTION


# ---------------------------------------------------------------------------
# parse_refute_verdict — robustness
# ---------------------------------------------------------------------------
def test_parse_strips_ansi_color_codes() -> None:
    raw = (
        "\x1b[32mFINAL:\x1b[0m "
        '{"verdict": "no_issue", "details": "ok", "severity": 0.0}'
    )
    v = parse_refute_verdict(raw)
    assert v.verdict_kind is RefuteVerdictKind.NO_ISSUE


def test_parse_skips_reasoning_prose_around_json() -> None:
    raw = (
        "Looking at this carefully I don't see issues...\n"
        '{"verdict": "no_issue", "details": "valid", "severity": 0.1}\n'
        "End of analysis."
    )
    v = parse_refute_verdict(raw)
    assert v.verdict_kind is RefuteVerdictKind.NO_ISSUE


def test_parse_picks_last_well_shaped_blob_when_multiple() -> None:
    raw = (
        '{"verdict": "fragile", "details": "first attempt", "severity": 0.5}\n'
        "Wait, on reflection:\n"
        '{"verdict": "counterexample", "details": "x=0", "severity": 0.9}'
    )
    v = parse_refute_verdict(raw)
    assert v.verdict_kind is RefuteVerdictKind.COUNTEREXAMPLE
    assert v.details == "x=0"


def test_parse_severity_clamps_above_one() -> None:
    v = parse_refute_verdict(
        '{"verdict": "fragile", "details": "x", "severity": 5.0}'
    )
    assert v.severity == 1.0


def test_parse_severity_clamps_below_zero() -> None:
    v = parse_refute_verdict(
        '{"verdict": "fragile", "details": "x", "severity": -2.0}'
    )
    assert v.severity == 0.0


def test_parse_accepts_integer_severity() -> None:
    v = parse_refute_verdict(
        '{"verdict": "fragile", "details": "x", "severity": 1}'
    )
    assert v.severity == 1.0


# ---------------------------------------------------------------------------
# parse_refute_verdict — error paths
# ---------------------------------------------------------------------------
def test_parse_no_json_raises() -> None:
    with pytest.raises(RefuteParseError) as exc_info:
        parse_refute_verdict("just some prose, no json here")
    assert exc_info.value.reason == "no_refute_json"


def test_parse_invalid_verdict_value_raises() -> None:
    with pytest.raises(RefuteParseError) as exc_info:
        parse_refute_verdict(
            '{"verdict": "bogus", "details": "x", "severity": 0.5}'
        )
    assert exc_info.value.reason == "invalid_verdict"


def test_parse_non_string_details_raises() -> None:
    with pytest.raises(RefuteParseError) as exc_info:
        parse_refute_verdict(
            '{"verdict": "no_issue", "details": 42, "severity": 0.0}'
        )
    assert exc_info.value.reason == "details_not_string"


def test_parse_non_numeric_severity_raises() -> None:
    with pytest.raises(RefuteParseError) as exc_info:
        parse_refute_verdict(
            '{"verdict": "no_issue", "details": "x", "severity": "high"}'
        )
    assert exc_info.value.reason == "severity_not_number"


def test_parse_boolean_severity_rejected() -> None:
    """``True`` is technically int in Python; reject it explicitly."""
    with pytest.raises(RefuteParseError) as exc_info:
        parse_refute_verdict(
            '{"verdict": "no_issue", "details": "x", "severity": true}'
        )
    assert exc_info.value.reason == "severity_not_number"


def test_parse_blob_missing_key_falls_through_to_no_blob_error() -> None:
    """A JSON object that lacks a required key isn't a 'verdict blob' at
    all — the brace walker keeps looking, eventually raising
    ``no_refute_json`` if no matching blob is found."""
    raw = '{"verdict": "no_issue", "details": "x"}'  # missing 'severity'
    with pytest.raises(RefuteParseError) as exc_info:
        parse_refute_verdict(raw)
    assert exc_info.value.reason == "no_refute_json"
