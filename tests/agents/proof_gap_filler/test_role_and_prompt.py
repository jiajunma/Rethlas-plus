"""GapFiller role + prompt-shape tests (issue #10)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import MockBackend
from rethlas_kb_agents.proof_gap_filler import GapFiller
from rethlas_kb_agents.proof_gap_filler.decoder import GapFillReviewParseError
from rethlas_kb_agents.proof_gap_filler.prompt import (
    PHASE_II_REPAIR_THRESHOLD,
    compose,
)
from rethlas_kb.adapter import ContextBundle
from tools.knowledge.models import Node


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    primary_topic: algebra
    topics: [algebra]
    ---

    # Group

    A set with associative multiplication, identity, and inverses.
    """)

LAGRANGE_MD = textwrap.dedent("""\
    ---
    id: algebra.lagrange
    title: Lagrange's Theorem
    kind: theorem
    status: staged
    uses:
      - algebra.group
    primary_topic: algebra
    topics: [algebra]
    ---

    # Lagrange's Theorem

    > **Theorem.** Let $G$ be a finite group and $H \\le G$. Then
    > $|H|$ divides $|G|$.

    **Proof.** TODO — coset equivalence relation argument.
    """)


@pytest.fixture
def adapter(tmp_path: Path) -> KbAdapter:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "staged" / "algebra" / "lagrange.md").write_text(LAGRANGE_MD)
    return KbAdapter(tmp_path)


def _node(**overrides) -> Node:
    base = dict(
        id="algebra.lagrange",
        title="Lagrange",
        kind="theorem",
        status="staged",
        uses=["algebra.group"],
        body="**Theorem.** ... **Proof.** TODO",
    )
    base.update(overrides)
    return Node(**base)


def _bundle() -> ContextBundle:
    return ContextBundle(
        target_id="algebra.lagrange", topic=None, mode="admitted+staged",
        nodes=[
            {"id": "algebra.group", "title": "Group", "kind": "definition",
             "status": "admitted", "evidence": "admitted",
             "body": "A set..."},
        ],
        raw={},
    )


# ---------------------------------------------------------------------------
# Prompt: generator discipline visible
# ---------------------------------------------------------------------------
def test_prompt_includes_anti_handwave_clause() -> None:
    out = compose(_node(), _bundle())
    assert "Anti-handwave" in out
    # The banned phrases are listed explicitly
    for phrase in ('"clearly"', '"obviously"', '"WLOG"', '"similarly"'):
        assert phrase in out


def test_prompt_includes_counterexample_first_step() -> None:
    out = compose(_node(), _bundle())
    assert "Try to refute first" in out


def test_prompt_fresh_attempt_block_when_no_prior_report() -> None:
    out = compose(_node(), _bundle())
    assert "fresh attempt" in out
    assert "repair" not in out.lower() or "fresh attempt" in out


def test_prompt_repair_block_when_prior_report_supplied() -> None:
    out = compose(_node(), _bundle(),
                  prior_verification_report="The proof has a gap at step 3.",
                  repair_count=1)
    assert "repair attempt" in out
    assert "Previous proof-verifier report" in out
    assert "step 3" in out


def test_prompt_phase_ii_drops_proof_body_at_threshold() -> None:
    out = compose(_node(), _bundle(),
                  prior_verification_report="prior fail",
                  repair_count=PHASE_II_REPAIR_THRESHOLD)
    assert "Phase II reroute" in out
    # Previous proof body is deliberately omitted
    assert "TODO" not in out  # node body's TODO is not in the prompt
    assert "materially different" in out


def test_prompt_phase_ii_does_not_include_prior_report_section() -> None:
    """Phase II drops both previous proof AND verifier report to avoid anchoring."""
    out = compose(_node(), _bundle(),
                  prior_verification_report="some prior fail report text",
                  repair_count=PHASE_II_REPAIR_THRESHOLD)
    assert "Previous proof-verifier report" not in out


def test_prompt_includes_output_contract() -> None:
    out = compose(_node(), _bundle())
    assert "Output contract" in out
    for key in ("decision", "filled_proof", "gap_remaining",
                "new_sublemmas", "suggested_approaches"):
        assert key in out


def test_prompt_includes_project_rules_when_supplied() -> None:
    out = compose(_node(), _bundle(),
                  project_rules="- Always use the Schreier-Sims notation.")
    assert "Additional project rules" in out
    assert "Schreier-Sims" in out


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------
def _filled_response(proof="**Proof.** ... $\\square$") -> str:
    return json.dumps({
        "decision": "filled",
        "rationale": "direct coset argument works",
        "filled_proof": proof,
        "confidence": 0.85,
    })


def test_run_returns_filled_review(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_filled_response())
    filler = GapFiller(backend=backend)
    r = filler.run("algebra.lagrange", adapter)
    assert r.is_filled
    assert backend.last_call["agent_role"] == "proof-gap-filler"


def test_run_passes_prior_report_into_prompt(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_filled_response())
    filler = GapFiller(backend=backend)
    filler.run(
        "algebra.lagrange", adapter,
        prior_verification_report="The verifier flagged step 3 unjustified.",
    )
    assert "step 3" in backend.last_call["prompt"]
    assert "repair attempt" in backend.last_call["prompt"]


def test_run_phase_ii_drops_proof_from_prompt(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_filled_response())
    filler = GapFiller(backend=backend)
    filler.run(
        "algebra.lagrange", adapter,
        prior_verification_report="prior fail",
        repair_count=PHASE_II_REPAIR_THRESHOLD,
    )
    prompt = backend.last_call["prompt"]
    assert "Phase II" in prompt
    assert "TODO" not in prompt  # previous proof body omitted


def test_run_picks_up_project_rules(adapter: KbAdapter) -> None:
    adapter.rules_dir.mkdir(parents=True, exist_ok=True)
    (adapter.rules_dir / "proof-gap-filler.md").write_text(
        "- Cite Fintzen-Yu correctly for tame depth-zero arguments.\n"
    )
    backend = MockBackend(canned_response=_filled_response())
    filler = GapFiller(backend=backend)
    filler.run("algebra.lagrange", adapter)
    assert "Fintzen-Yu" in backend.last_call["prompt"]


def test_run_surfaces_parse_error(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response="not json")
    filler = GapFiller(backend=backend)
    with pytest.raises(GapFillReviewParseError):
        filler.run("algebra.lagrange", adapter)


# ---------------------------------------------------------------------------
# Adapter: update_staged_node_body
# ---------------------------------------------------------------------------
def test_update_staged_node_body_replaces_body_keeps_frontmatter(
    adapter: KbAdapter,
) -> None:
    new_body = "# Lagrange\n\nNew proof body — coset argument complete."
    path = adapter.update_staged_node_body("algebra.lagrange", new_body)
    text = path.read_text()
    assert "New proof body" in text
    assert "TODO" not in text  # old body gone
    # Frontmatter preserved
    assert "id: algebra.lagrange" in text
    assert "status: staged" in text
    # Round-trip still parses
    node = adapter.read_node("algebra.lagrange")
    assert "coset argument complete" in node.body


def test_update_staged_node_body_refuses_admitted_node(adapter: KbAdapter) -> None:
    with pytest.raises(KeyError, match="not in a staged status"):
        adapter.update_staged_node_body("algebra.group", "new body")


def test_update_staged_node_body_missing_node_raises(adapter: KbAdapter) -> None:
    with pytest.raises(KeyError, match="not found"):
        adapter.update_staged_node_body("algebra.nonexistent", "x")
