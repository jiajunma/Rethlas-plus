"""M9 — dashboard server helpers for verifier-status surfacing.

Covers the two small parsers that ``DashboardCore.node_detail`` uses to
expose verifier verdict + verification_report shape to the frontend:

- ``_safe_parse_verification_report`` parses the Node row's
  ``verification_report`` column (a serialized JSON string, may be
  empty / corrupt / non-string)
- ``_summarize_event`` builds the per-event entry for ``recent_events``
  with type-specific summary fields (verdict + counts for verifier
  events; node_count + target for generator batches; etc.)
"""

from __future__ import annotations

import json

from dashboard.server import (
    _safe_parse_verification_report,
    _summarize_event,
)


def test_safe_parse_verification_report_empty_returns_none() -> None:
    assert _safe_parse_verification_report("") is None
    assert _safe_parse_verification_report(None) is None  # type: ignore[arg-type]


def test_safe_parse_verification_report_non_string_returns_none() -> None:
    assert _safe_parse_verification_report({"already": "object"}) is None  # type: ignore[arg-type]
    assert _safe_parse_verification_report(42) is None  # type: ignore[arg-type]


def test_safe_parse_verification_report_corrupt_json_returns_none() -> None:
    assert _safe_parse_verification_report("not json at all") is None
    assert _safe_parse_verification_report('{"unclosed":') is None


def test_safe_parse_verification_report_valid_json_returns_dict() -> None:
    raw = json.dumps({"checked_items": [], "summary": "ok"})
    parsed = _safe_parse_verification_report(raw)
    assert parsed == {"checked_items": [], "summary": "ok"}


def test_safe_parse_verification_report_non_dict_top_level_returns_none() -> None:
    """A JSON array at the top level isn't a valid report — return None
    so the frontend falls back gracefully instead of trying to read
    .checked_items off a list."""
    assert _safe_parse_verification_report("[1, 2, 3]") is None


def test_summarize_event_verifier_run_completed_string_report() -> None:
    """Older event writers serialize ``verification_report`` as a JSON
    string. The summary must still extract verdict + counts."""
    body = {
        "event_id": "20260427T000000.000-0001-aaaa",
        "type": "verifier.run_completed",
        "actor": "verifier:codex-default",
        "ts": "2026-04-27T00:00:00.000Z",
        "target": "lem:foo",
        "payload": {
            "verdict": "gap",
            "verification_report": json.dumps({
                "checked_items": [{"location": "p1", "status": "gap", "notes": "n1"}],
                "gaps": [{"location": "p1", "issue": "missing dep"}],
                "critical_errors": [],
                "external_reference_checks": [
                    {"reference": "\\ref{lem:bar}", "status": "missing_from_nodes",
                     "location": "p1", "notes": "—"},
                    {"reference": "\\ref{def:x}", "status": "verified_in_nodes",
                     "location": "p1", "notes": "—"},
                ],
                "summary": "incomplete proof",
            }),
        },
    }
    s = _summarize_event(body)
    assert s["type"] == "verifier.run_completed"
    assert s["actor"] == "verifier:codex-default"
    assert s["ts"] == "2026-04-27T00:00:00.000Z"
    assert s["summary"]["verdict"] == "gap"
    assert s["summary"]["gap_count"] == 1
    assert s["summary"]["critical_count"] == 0
    assert s["summary"]["report_summary"] == "incomplete proof"
    # Only one of the two external refs is unresolved.
    assert s["summary"]["ext_ref_issue_count"] == 1


def test_summarize_event_verifier_run_completed_dict_report() -> None:
    """Newer event writers may carry the report as an object directly.
    The summary must accept both shapes."""
    body = {
        "type": "verifier.run_completed",
        "ts": "2026-04-27T00:00:00.000Z",
        "payload": {
            "verdict": "accepted",
            "verification_report": {
                "checked_items": [],
                "gaps": [],
                "critical_errors": [],
                "external_reference_checks": [],
                "summary": "all good",
            },
        },
    }
    s = _summarize_event(body)
    assert s["summary"]["verdict"] == "accepted"
    assert s["summary"]["gap_count"] == 0
    assert s["summary"]["critical_count"] == 0
    assert s["summary"]["report_summary"] == "all good"
    assert s["summary"]["ext_ref_issue_count"] == 0


def test_summarize_event_verifier_run_completed_missing_report_falls_back() -> None:
    """A malformed payload (no parseable report) should still produce a
    valid entry — verdict known, but counts absent."""
    body = {
        "type": "verifier.run_completed",
        "payload": {"verdict": "critical", "verification_report": "oops not json"},
    }
    s = _summarize_event(body)
    assert s["summary"]["verdict"] == "critical"
    assert "gap_count" not in s["summary"]


def test_summarize_event_generator_batch_committed() -> None:
    body = {
        "type": "generator.batch_committed",
        "payload": {
            "target": "thm:goal",
            "nodes": [
                {"label": "thm:goal"},
                {"label": "lem:helper"},
                {"label": "def:base"},
            ],
        },
    }
    s = _summarize_event(body)
    assert s["summary"]["node_count"] == 3
    assert s["summary"]["target"] == "thm:goal"


def test_summarize_event_user_hint_attached_truncates_excerpt() -> None:
    long_hint = "x" * 500
    body = {
        "type": "user.hint_attached",
        "payload": {"hint": long_hint},
    }
    s = _summarize_event(body)
    assert len(s["summary"]["hint_excerpt"]) == 200


def test_summarize_event_unknown_type_has_empty_summary() -> None:
    body = {
        "event_id": "e",
        "type": "user.node_added",
        "payload": {},
    }
    s = _summarize_event(body)
    assert s["type"] == "user.node_added"
    assert s["summary"] == {}
