"""Prompt-composition tests (issue #7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rethlas_kb.adapter import ContextBundle
from rethlas_kb_agents.statement_verifier.prompt import (
    _render_context,
    _render_target,
    compose,
)
from tools.knowledge.models import Node


def _node(**overrides: object) -> Node:
    base = dict(
        id="algebra.quotient_group",
        title="Quotient Group",
        kind="definition",
        status="staged",
        uses=["algebra.group", "algebra.normal_subgroup"],
        body=(
            "Let $G$ be a group and $N$ a normal subgroup. "
            "Define $G/N$ as the set of left cosets."
        ),
        file_path=Path("docs/knowledge/staged/algebra/quotient_group.md"),
    )
    base.update(overrides)
    return Node(**base)


def _bundle(
    nodes: list[dict] | None = None,
    *,
    target_id: str = "algebra.quotient_group",
    mode: str = "admitted+staged",
) -> ContextBundle:
    return ContextBundle(
        target_id=target_id,
        topic=None,
        mode=mode,
        nodes=nodes or [],
        raw={},
    )


# ---------------------------------------------------------------------------
# compose() — full prompt
# ---------------------------------------------------------------------------
def test_compose_includes_role_block_and_decisions() -> None:
    out = compose(_node(), _bundle())
    assert "statement-verifier" in out
    for decision in ("accepted", "needs_definition",
                     "generality_concern", "formulation_issue",
                     "context_insufficient"):
        assert decision in out


def test_compose_includes_output_contract_with_required_keys() -> None:
    out = compose(_node(), _bundle())
    assert "Output contract" in out
    for key in ("decision", "rationale", "confidence",
                "quoted_statement",
                "missing_definitions", "formulation_issues",
                "generality_notes", "context_gap_notes"):
        assert key in out


def test_compose_includes_conservative_stance_block() -> None:
    """QED-style 'when in doubt, flag' instruction must reach the LLM."""
    out = compose(_node(), _bundle())
    assert "Conservative stance" in out
    # Spell out the bias-toward-flagging guidance, in the agent's own
    # words — these substrings are how we keep the model from quietly
    # picking 'accepted' under uncertainty.
    assert "Under uncertainty" in out
    assert "context_insufficient" in out


def test_compose_includes_anti_pattern_catalog() -> None:
    """Explicit checklist of formulation defects (QED structural Check 2)."""
    out = compose(_node(), _bundle())
    # The numbered catalog header
    assert "Anti-pattern catalog" in out
    # Each individual anti-pattern category should be named so the
    # model has something concrete to scan for.
    for pattern in (
        "Quantifier drift",
        "Domain restriction",
        "Strengthened or weakened hypotheses",
        "Missing uniqueness",
        "Implicit regularity",
        "Swapped conclusion",
        "Dangling notation",
        "Modified constants",
    ):
        assert pattern in out


def test_compose_includes_verbatim_quote_instruction() -> None:
    """Output schema demands a verbatim quote so the LLM can't paraphrase-and-agree."""
    out = compose(_node(), _bundle())
    # The output_contract block names the quoted_statement field and
    # says the rationale must reference it.
    assert "verbatim" in out
    assert "quoted_statement" in out


def test_compose_includes_confidence_guidance() -> None:
    """Numeric confidence is meaningless without calibration anchors."""
    out = compose(_node(), _bundle())
    assert "Confidence guidance" in out
    # The four band thresholds spell out what each range means
    assert "0.9" in out
    assert "0.5" in out


def test_compose_includes_node_id_title_kind_and_body() -> None:
    out = compose(_node(), _bundle())
    assert "algebra.quotient_group" in out
    assert "Quotient Group" in out
    assert "definition" in out
    assert "normal subgroup" in out  # from body


def test_compose_lists_uses_when_present() -> None:
    out = compose(_node(), _bundle())
    assert "algebra.group" in out


def test_compose_omits_uses_line_when_empty() -> None:
    out = compose(_node(uses=[]), _bundle())
    # 'uses' as a heading shouldn't appear when there are none
    assert "**uses**" not in out


def test_compose_renders_context_predecessor_nodes() -> None:
    bundle = _bundle(nodes=[
        {"id": "algebra.group", "title": "Group", "kind": "definition",
         "status": "admitted", "evidence": "admitted",
         "body": "A group is..."},
        {"id": "algebra.quotient_group", "title": "Quotient Group",
         "kind": "definition", "status": "staged",
         "evidence": "non-admitted", "body": "(target body)"},
    ])
    out = compose(_node(), bundle)
    assert "algebra.group" in out
    # Target itself must not be re-rendered in the context section
    target_section = out.split("## Context", 1)[1]
    assert "(target body)" not in target_section


def test_compose_marks_non_admitted_evidence() -> None:
    bundle = _bundle(nodes=[
        {"id": "algebra.lemma_x", "title": "Lemma X", "kind": "lemma",
         "status": "staged", "evidence": "non-admitted",
         "body": "Some draft lemma."},
    ])
    out = compose(_node(), bundle)
    assert "non-admitted evidence" in out


def test_compose_handles_empty_context() -> None:
    out = compose(_node(), _bundle(nodes=[]))
    assert "no admitted predecessors in scope" in out


# ---------------------------------------------------------------------------
# Renderers (unit-level)
# ---------------------------------------------------------------------------
def test_render_target_uses_empty_body_placeholder() -> None:
    out = _render_target(_node(body=""))
    assert "node body is empty" in out


def test_render_context_shows_mode_line() -> None:
    out = _render_context(_bundle(
        mode="admitted+staged",
        nodes=[{"id": "x.y", "title": "X", "kind": "lemma",
                "status": "admitted", "evidence": "admitted",
                "body": "hello"}],
    ))
    assert "admitted+staged" in out


# ---------------------------------------------------------------------------
# compose output is a single string
# ---------------------------------------------------------------------------
def test_compose_returns_str_and_ends_with_newline() -> None:
    out = compose(_node(), _bundle())
    assert isinstance(out, str)
    assert out.endswith("\n")
