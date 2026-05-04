from __future__ import annotations

import json

import pytest

from referee.decoder import RefereeDecodeError, parse_referee_report


def _report(**overrides):
    base = {
        "output_schema": "referee_report_v1",
        "review_id": "review_toy_001",
        "target": "thm:toy",
        "workspace_path": "reviews/review_toy_001",
        "target_hashes": {"statement_hash": "sha256:" + "a" * 64},
        "verdict": "needs_revision",
        "checked_claims": [],
        "reconstructed_jumps": [],
        "generated_repairs": [],
        "verified_repairs": [],
        "unresolved_gaps": [],
        "requested_details": [],
        "issues": [],
        "counterexample_attempts": [],
        "external_reference_checks": [
            {
                "citation_key": "A",
                "applicability": "resolved_exact",
                "evidence_hash": "sha256:" + "b" * 64,
                "quoted_or_paraphrased_statement": "The cited theorem says exactly X.",
            }
        ],
        "extraction_quality_checks": [],
        "recommended_kb_updates": [],
        "summary": "needs a local lemma",
    }
    base.update(overrides)
    return base


def test_referee_report_accepts_review_record() -> None:
    parsed = parse_referee_report(json.dumps(_report()))
    assert parsed.review_id == "review_toy_001"
    assert parsed.event_payload(report_hash="sha256:x")["verdict"] == "needs_revision"


def test_referee_checked_citation_requires_evidence_hash() -> None:
    report = _report()
    del report["external_reference_checks"][0]["evidence_hash"]
    with pytest.raises(RefereeDecodeError, match="citation_evidence_required"):
        parse_referee_report(json.dumps(report))


def test_referee_requested_detail_blocks_acceptance() -> None:
    report = _report(
        verdict="accepted",
        requested_details=[
            {
                "issue_type": "requested_detail",
                "severity": "major",
                "requested_detail": "State the missing reduction lemma.",
                "blocks_verdict": True,
            }
        ],
    )
    with pytest.raises(RefereeDecodeError, match="requested_detail_blocks_acceptance"):
        parse_referee_report(json.dumps(report))


def test_referee_report_path_must_stay_under_reviews() -> None:
    report = _report(workspace_path="knowledge_base/nodes/thm_bad.md")
    with pytest.raises(RefereeDecodeError, match="workspace_path"):
        parse_referee_report(json.dumps(report))
