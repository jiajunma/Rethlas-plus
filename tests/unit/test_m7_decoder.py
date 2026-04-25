"""M7 — verifier verdict decoder unit tests."""

from __future__ import annotations

import json

import pytest

from verifier.decoder import Verdict, VerdictParseError, parse_verdict


def _verdict(verdict: str, *, gaps=None, crit=None) -> dict:
    return {
        "verdict": verdict,
        "verification_hash": "sha256:" + ("a" * 64),
        "verification_report": {
            "summary": "ok" if verdict == "accepted" else "issues",
            "checked_items": [],
            "gaps": gaps or [],
            "critical_errors": crit or [],
            "external_reference_checks": [],
        },
        "repair_hint": "" if verdict == "accepted" else "look at step 4",
    }


def test_parse_accepted_verdict() -> None:
    raw = json.dumps(_verdict("accepted"))
    v = parse_verdict(raw)
    assert v.is_accepted
    assert v.verification_hash.startswith("sha256:")


def test_parse_gap_verdict_requires_gaps() -> None:
    raw = json.dumps(_verdict("gap", gaps=[{"step": 1, "reason": "incomplete"}]))
    v = parse_verdict(raw)
    assert v.verdict == "gap"


def test_parse_critical_verdict_requires_crit() -> None:
    raw = json.dumps(_verdict("critical", crit=[{"step": 2, "reason": "wrong"}]))
    v = parse_verdict(raw)
    assert v.verdict == "critical"


def test_ansi_codes_stripped_before_parse() -> None:
    raw = "\x1b[1mreasoning prefix...\x1b[0m\n" + json.dumps(_verdict("accepted"))
    v = parse_verdict(raw)
    assert v.is_accepted


def test_last_blob_wins_when_multiple() -> None:
    """Reasoning trace prints intermediate {} blobs; only the LAST verdict counts."""
    intermediate = json.dumps({"verdict": "gap", "verification_hash": "x", "verification_report": {}, "repair_hint": ""})
    final = json.dumps(_verdict("accepted"))
    raw = f"intermediate verdict: {intermediate}\n\nfinal verdict:\n{final}\n"
    v = parse_verdict(raw)
    assert v.is_accepted


def test_no_verdict_blob_raises() -> None:
    with pytest.raises(VerdictParseError) as ei:
        parse_verdict("just reasoning, no JSON object here")
    assert ei.value.reason == "no_verdict_json"


def test_invalid_verdict_value_raises() -> None:
    bad = {**_verdict("accepted"), "verdict": "maybe"}
    with pytest.raises(VerdictParseError) as ei:
        parse_verdict(json.dumps(bad))
    assert ei.value.reason == "invalid_verdict"


def test_missing_report_field_raises() -> None:
    bad = _verdict("accepted")
    bad["verification_report"].pop("summary")
    with pytest.raises(VerdictParseError):
        parse_verdict(json.dumps(bad))


def test_consistency_accepted_with_gaps_raises() -> None:
    bad = _verdict("accepted", gaps=[{"step": 1}])
    with pytest.raises(VerdictParseError) as ei:
        parse_verdict(json.dumps(bad))
    assert ei.value.reason == "verdict_consistency"


def test_consistency_gap_without_gaps_raises() -> None:
    bad = _verdict("gap")  # gaps default empty
    with pytest.raises(VerdictParseError) as ei:
        parse_verdict(json.dumps(bad))
    assert ei.value.reason == "verdict_consistency"


def test_consistency_critical_without_crit_raises() -> None:
    bad = _verdict("critical")
    with pytest.raises(VerdictParseError) as ei:
        parse_verdict(json.dumps(bad))
    assert ei.value.reason == "verdict_consistency"


def test_braces_inside_strings_dont_confuse_matcher() -> None:
    """Strings inside the JSON may contain ``{`` / ``}`` characters."""
    blob = _verdict("gap", gaps=[{"description": "issue with {x : y}"}])
    raw = json.dumps(blob)
    v = parse_verdict(raw)
    assert v.verdict == "gap"
