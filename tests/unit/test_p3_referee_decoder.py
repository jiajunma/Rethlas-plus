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
        "theorem_nodes": [],
        "theorem_dependency_edges": [],
        "node_location_notes": [],
        "typo_findings": [],
        "recommended_kb_updates": [],
        "summary": "needs a local lemma",
    }
    base.update(overrides)
    return base


def test_referee_report_accepts_review_record() -> None:
    parsed = parse_referee_report(json.dumps(_report()))
    assert parsed.review_id == "review_toy_001"
    assert parsed.event_payload(report_hash="sha256:x")["verdict"] == "needs_revision"


def test_referee_report_preserves_theorem_graph_and_typos() -> None:
    parsed = parse_referee_report(
        json.dumps(
            _report(
                theorem_nodes=[
                    {
                        "label": "thm:toy_main",
                        "kind": "theorem",
                        "title": "主定理",
                        "statement": "S.",
                        "source_excerpt": "Theorem. For $x$, $f(x)=x$.",
                        "formula_excerpt": "$f(x)=x$",
                        "display_source_excerpt": "Theorem. For \\(x\\), \\(f(x)=x\\).",
                        "display_formula_excerpt": "\\[f(x)=x\\]",
                        "typesetting_notes": "Display fields transcribe source math to TeX.",
                        "status": "conditional",
                        "extraction_kind": "explicit_environment",
                    }
                ],
                theorem_dependency_edges=[
                    {
                        "dependency": "assump:toy_input",
                        "dependent": "thm:toy_main",
                        "relation": "assumes",
                    }
                ],
                node_location_notes=[
                    {
                        "label": "thm:toy_main",
                        "locator": "PDF p. 10, Theorem 1.1",
                        "note": "主定理位置。",
                    }
                ],
                typo_findings=[
                    {
                        "typo_id": "typo:toy:p10",
                        "severity": "major",
                        "locator": "PDF p. 10",
                        "observed": "x",
                        "suggested": "y",
                        "reason": "notation drift",
                    }
                ],
            )
        )
    )
    assert parsed.theorem_nodes[0]["label"] == "thm:toy_main"
    assert parsed.theorem_dependency_edges[0]["dependent"] == "thm:toy_main"
    assert parsed.node_location_notes[0]["locator"] == "PDF p. 10, Theorem 1.1"
    assert parsed.typo_findings[0]["typo_id"] == "typo:toy:p10"
    assert parsed.to_dict()["typo_findings"][0]["suggested"] == "y"
    assert parsed.theorem_nodes[0]["source_excerpt"].startswith("Theorem.")
    assert parsed.theorem_nodes[0]["formula_excerpt"] == "$f(x)=x$"
    assert parsed.theorem_nodes[0]["display_formula_excerpt"] == "\\[f(x)=x\\]"
    assert parsed.theorem_nodes[0]["typesetting_notes"].startswith("Display fields")


def test_referee_report_preserves_remark_and_implicit_paragraph_nodes() -> None:
    parsed = parse_referee_report(
        json.dumps(
            _report(
                theorem_nodes=[
                    {
                        "label": "rem:toy_warning",
                        "kind": "remark",
                        "title": "关于记号的说明",
                        "statement": "此处说明后文默认使用的记号。",
                        "status": "context",
                        "extraction_kind": "explicit_environment",
                        "source_locator": "PDF p. 12, Remark 2.1",
                        "source_note": "显式 remark，也应作为 review graph 的节点。",
                    },
                    {
                        "label": "def:toy_implicit",
                        "kind": "definition",
                        "title": "自然段中的隐式定义",
                        "statement": "自然段引入一个后文反复使用的对象。",
                        "status": "source_claim",
                        "extraction_kind": "implicit_paragraph",
                        "source_locator": "PDF p. 13, paragraph after (2.2)",
                        "source_note": "该自然段没有定义环境，但承担定义功能。",
                        "promotion_confidence": 0.84,
                        "overpromotion_risk": False,
                    },
                ],
            )
        )
    )
    assert parsed.theorem_nodes[0]["kind"] == "remark"
    assert parsed.theorem_nodes[1]["extraction_kind"] == "implicit_paragraph"
    assert parsed.theorem_nodes[1]["source_locator"] == "PDF p. 13, paragraph after (2.2)"
    assert parsed.to_dict()["theorem_nodes"][1]["source_note"].startswith("该自然段")


def test_referee_report_rejects_invalid_graph_status_and_relation() -> None:
    with pytest.raises(RefereeDecodeError, match="invalid theorem_nodes"):
        parse_referee_report(
            json.dumps(
                _report(
                    theorem_nodes=[
                        {
                            "label": "thm:toy_main",
                            "kind": "theorem",
                            "status": "maybe_true",
                        }
                    ]
                )
            )
        )

    with pytest.raises(RefereeDecodeError, match="invalid theorem_dependency_edges"):
        parse_referee_report(
            json.dumps(
                _report(
                    theorem_dependency_edges=[
                        {
                            "dependency": "lem:a",
                            "dependent": "thm:b",
                            "relation": "handwaves_to",
                        }
                    ]
                )
            )
        )


def test_referee_report_requires_locator_and_note_for_implicit_paragraph() -> None:
    with pytest.raises(RefereeDecodeError, match="implicit paragraph.*source_locator"):
        parse_referee_report(
            json.dumps(
                _report(
                    theorem_nodes=[
                        {
                            "label": "def:toy_implicit",
                            "kind": "definition",
                            "extraction_kind": "implicit_paragraph",
                            "source_note": "Functions as a definition.",
                        }
                    ]
                )
            )
        )

    with pytest.raises(RefereeDecodeError, match="implicit paragraph.*source_note"):
        parse_referee_report(
            json.dumps(
                _report(
                    theorem_nodes=[
                        {
                            "label": "def:toy_implicit",
                            "kind": "definition",
                            "extraction_kind": "implicit_paragraph",
                            "source_locator": "PDF p. 13, paragraph 2",
                        }
                    ]
                )
            )
        )


def test_referee_report_requires_scope_for_external_theorem_nodes() -> None:
    with pytest.raises(RefereeDecodeError, match="external theorem_nodes"):
        parse_referee_report(
            json.dumps(
                _report(
                    theorem_nodes=[
                        {
                            "label": "ext:toy_reference",
                            "kind": "external_theorem",
                            "status": "review_only",
                        }
                    ]
                )
            )
        )

    parsed = parse_referee_report(
        json.dumps(
            _report(
                theorem_nodes=[
                    {
                        "label": "ext:toy_reference",
                        "kind": "external_theorem",
                        "status": "review_only",
                        "scope": "review_only",
                    }
                ]
            )
        )
    )
    assert parsed.theorem_nodes[0]["scope"] == "review_only"


def test_referee_report_allows_legacy_missing_graph_fields() -> None:
    report = _report()
    for key in (
        "theorem_nodes",
        "theorem_dependency_edges",
        "node_location_notes",
        "typo_findings",
    ):
        del report[key]
    parsed = parse_referee_report(json.dumps(report))
    assert parsed.theorem_nodes == ()
    assert parsed.theorem_dependency_edges == ()
    assert parsed.node_location_notes == ()
    assert parsed.typo_findings == ()


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
