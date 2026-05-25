"""StatementVerifier role tests (issue #7).

Uses MockBackend to feed canned responses; uses a tmp_path KB to
exercise the adapter → prompt → backend → decoder pipeline end-to-end.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import MockBackend
from rethlas_kb_agents.statement_verifier import (
    AGENT_ROLE,
    StatementReview,
    StatementReviewParseError,
    StatementVerifier,
)
from rethlas_kb_agents.statement_verifier.role import AGENT_ROLE as ROLE_CONST


# ---------------------------------------------------------------------------
# Inline KB fixture (mirrors test_adapter.py but smaller)
# ---------------------------------------------------------------------------
GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    uses: []
    primary_topic: algebra
    topics: [algebra]
    ---

    # Group

    A set with associative multiplication, identity, and inverses.
    """)

QUOTIENT_MD = textwrap.dedent("""\
    ---
    id: algebra.quotient_group
    title: Quotient Group
    kind: definition
    status: staged
    uses:
      - algebra.group
    primary_topic: algebra
    topics: [algebra]
    ---

    # Quotient Group

    Let $G$ be a group and $N$ a normal subgroup. $G/N$ is the set of cosets.
    """)


@pytest.fixture
def adapter(tmp_path: Path) -> KbAdapter:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(
        QUOTIENT_MD
    )
    return KbAdapter(tmp_path)


# ---------------------------------------------------------------------------
# Construction + name
# ---------------------------------------------------------------------------
def test_role_constant_matches_protocol() -> None:
    assert ROLE_CONST == "statement-verifier"
    assert AGENT_ROLE == "statement-verifier"


def test_role_name_is_statement_verifier(adapter: KbAdapter) -> None:
    verifier = StatementVerifier(backend=MockBackend())
    assert verifier.name == "statement-verifier"


# ---------------------------------------------------------------------------
# Happy-path .run()
# ---------------------------------------------------------------------------
def test_run_returns_statement_review_from_canned_response(
    adapter: KbAdapter,
) -> None:
    canned = json.dumps({
        "decision": "accepted",
        "rationale": "Quotient group is well-formed.",
        "confidence": 0.85,
    })
    backend = MockBackend(canned_response=canned)
    verifier = StatementVerifier(backend=backend)

    review = verifier.run("algebra.quotient_group", adapter)

    assert isinstance(review, StatementReview)
    assert review.decision == "accepted"
    assert review.rationale == "Quotient group is well-formed."
    assert review.confidence == 0.85
    assert review.is_accepted


def test_run_passes_correct_agent_role_to_backend(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    verifier.run("algebra.quotient_group", adapter)
    assert backend.last_call["agent_role"] == "statement-verifier"


def test_run_includes_node_body_in_prompt(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    verifier.run("algebra.quotient_group", adapter)
    prompt = backend.last_call["prompt"]
    assert "Quotient Group" in prompt
    assert "normal subgroup" in prompt
    # Predecessor body must also be in the prompt (context pack closure)
    assert "Group" in prompt
    assert "identity" in prompt  # from algebra.group body


def test_run_propagates_custom_timeout(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend, timeout_seconds=42)
    verifier.run("algebra.quotient_group", adapter)
    assert backend.last_call["timeout_seconds"] == 42


def test_run_uses_include_staged_context_flag(adapter: KbAdapter) -> None:
    """When include_staged_context=False, the prompt mode flips to admitted."""
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend, include_staged_context=False)
    # Picking an admitted node so context_pack doesn't error on
    # all-staged scope.
    verifier.run("algebra.group", adapter)
    prompt = backend.last_call["prompt"]
    assert "Mode: **admitted**" in prompt


def test_run_default_include_staged_marks_staged_evidence(
    adapter: KbAdapter,
) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    verifier.run("algebra.quotient_group", adapter)
    prompt = backend.last_call["prompt"]
    assert "admitted+staged" in prompt


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------
def test_run_raises_on_unparseable_backend_output(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response="just prose, no JSON")
    verifier = StatementVerifier(backend=backend)
    with pytest.raises(StatementReviewParseError, match="no_review_json"):
        verifier.run("algebra.quotient_group", adapter)


def test_run_raises_keyerror_on_missing_node(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    with pytest.raises(KeyError):
        verifier.run("algebra.does_not_exist", adapter)


def test_run_decoder_failure_does_not_swallow_backend_response(
    adapter: KbAdapter,
) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "invalid_one", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    with pytest.raises(StatementReviewParseError, match="invalid_decision"):
        verifier.run("algebra.quotient_group", adapter)


# ---------------------------------------------------------------------------
# Project-rules sidecar wiring (issue #22)
# ---------------------------------------------------------------------------
def test_run_injects_project_rules_into_prompt(adapter: KbAdapter) -> None:
    """Both global and role-specific rules files land in the prompt."""
    adapter.rules_dir.mkdir(parents=True, exist_ok=True)
    (adapter.rules_dir / "_global.md").write_text(
        "- Project-wide notation: `(G,N)` denotes group / normal subgroup pair.\n"
    )
    (adapter.rules_dir / "statement-verifier.md").write_text(
        "- Reject any cardinality claim without a finiteness hypothesis.\n"
    )
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    verifier.run("algebra.quotient_group", adapter)
    prompt = backend.last_call["prompt"]
    assert "Additional project rules" in prompt
    assert "(G,N)" in prompt
    assert "cardinality claim" in prompt


def test_run_does_not_add_rules_section_when_no_files(
    adapter: KbAdapter,
) -> None:
    backend = MockBackend(canned_response=json.dumps({
        "decision": "accepted", "rationale": "ok"}))
    verifier = StatementVerifier(backend=backend)
    verifier.run("algebra.quotient_group", adapter)
    assert "Additional project rules" not in backend.last_call["prompt"]
