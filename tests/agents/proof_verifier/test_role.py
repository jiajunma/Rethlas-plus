"""ProofVerifier role + pipeline tests (issue #9)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import MockBackend
from rethlas_kb_agents.proof_verifier import ProofVerifier
from rethlas_kb_agents.proof_verifier.decoder import ProofReviewParseError


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

    **Proof.** Define an equivalence relation on $G$ by $x \\sim y$ iff
    $xy^{-1} \\in H$. Each equivalence class has size $|H|$. Hence $|G|$
    is a multiple of $|H|$. $\\square$
    """)


@pytest.fixture
def adapter(tmp_path: Path) -> KbAdapter:
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "staged" / "algebra" / "lagrange.md").write_text(LAGRANGE_MD)
    return KbAdapter(tmp_path)


# ---------------------------------------------------------------------------
# Single-stage entry points
# ---------------------------------------------------------------------------
def _judge_response(difficulty="hard", **kw) -> str:
    p = {"difficulty": difficulty, "rationale": "looks ok"}
    p.update(kw)
    return json.dumps(p)


def _structural_response(verdict="pass") -> str:
    return json.dumps({
        "verdict": verdict,
        "rationale": "ok",
        "checks": [
            {"name": "statement_quality", "verdict": verdict},
            {"name": "alignment", "verdict": "pass"},
            {"name": "completeness", "verdict": "pass"},
            {"name": "architecture", "verdict": "pass"},
        ],
    })


def _detailed_response(verdict="accepted") -> str:
    p = {
        "verdict": verdict,
        "rationale": "ok",
        "step_verdicts": [
            {"step_id": "1", "claim": "equivalence relation", "verdict": "pass"},
        ],
    }
    if verdict == "gap":
        p["step_verdicts"].append({
            "step_id": "2", "claim": "class size", "verdict": "fail",
            "notes": "size argument incomplete",
        })
    return json.dumps(p)


def test_run_judge_returns_judge_verdict(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_judge_response("easy", verdict="accepted"))
    verifier = ProofVerifier(backend=backend)
    v = verifier.run_judge("algebra.lagrange", adapter)
    assert v.difficulty == "easy"
    assert v.verdict == "accepted"
    assert backend.last_call["agent_role"] == "proof-verifier"


def test_run_structural_returns_structural_verdict(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_structural_response())
    verifier = ProofVerifier(backend=backend)
    v = verifier.run_structural("algebra.lagrange", adapter)
    assert v.verdict == "pass"
    assert v.passed


def test_run_detailed_returns_detailed_verdict(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_detailed_response())
    verifier = ProofVerifier(backend=backend)
    v = verifier.run_detailed("algebra.lagrange", adapter)
    assert v.verdict == "accepted"


# ---------------------------------------------------------------------------
# Pipeline: auto depth
# ---------------------------------------------------------------------------
def test_pipeline_auto_easy_short_circuits_at_judge(adapter: KbAdapter) -> None:
    """Easy judge → pipeline terminates at judge; no structural/detailed runs."""
    backend = MockBackend(
        canned_response=_judge_response("easy", verdict="accepted"),
    )
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="auto")
    assert review.final_verdict == "accepted"
    assert review.decisive_stage == "judge"
    assert review.short_circuited_at == "judge_easy"
    assert review.stages_run == ["judge"]
    assert backend.call_count == 1


def test_pipeline_auto_hard_then_structural_fail_short_circuits(
    adapter: KbAdapter,
) -> None:
    """Hard judge → structural runs; if structural fails, detailed skipped."""
    responses = iter([
        _judge_response("hard"),
        _structural_response("fail").replace(
            '"name": "statement_quality", "verdict": "fail"',
            '"name": "statement_quality", "verdict": "fail", "notes": "drift"',
        ),
    ])
    backend = _SequencedBackend(responses)
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="auto")
    assert review.final_verdict == "gap"
    assert review.decisive_stage == "structural"
    assert review.short_circuited_at == "structural_fail"
    assert review.stages_run == ["judge", "structural"]
    assert backend.call_count == 2


def test_pipeline_auto_full_path_runs_all_three(adapter: KbAdapter) -> None:
    responses = iter([
        _judge_response("hard"),
        _structural_response("pass"),
        _detailed_response("accepted"),
    ])
    backend = _SequencedBackend(responses)
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="auto")
    assert review.final_verdict == "accepted"
    assert review.decisive_stage == "detailed"
    assert review.short_circuited_at is None
    assert review.stages_run == ["judge", "structural", "detailed"]
    assert backend.call_count == 3


def test_pipeline_auto_full_path_detailed_gap(adapter: KbAdapter) -> None:
    responses = iter([
        _judge_response("hard"),
        _structural_response("pass"),
        _detailed_response("gap"),
    ])
    backend = _SequencedBackend(responses)
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="auto")
    assert review.final_verdict == "gap"
    assert review.detailed is not None


# ---------------------------------------------------------------------------
# Pipeline: explicit depths
# ---------------------------------------------------------------------------
def test_pipeline_depth_easy_caps_at_judge_even_when_hard(
    adapter: KbAdapter,
) -> None:
    backend = MockBackend(canned_response=_judge_response("hard"))
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="easy")
    # Capped depth → uncertain
    assert review.final_verdict == "uncertain"
    assert review.decisive_stage == "judge"
    assert review.short_circuited_at == "depth_easy_cap"
    assert backend.call_count == 1


def test_pipeline_depth_structural_skips_judge(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response=_structural_response("pass"))
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="structural")
    assert review.judge is None
    assert review.structural is not None
    assert review.final_verdict == "uncertain"  # structural pass alone is not a final accept


def test_pipeline_depth_detailed_skips_judge_and_structural(
    adapter: KbAdapter,
) -> None:
    backend = MockBackend(canned_response=_detailed_response("accepted"))
    verifier = ProofVerifier(backend=backend)
    review = verifier.run("algebra.lagrange", adapter, depth="detailed")
    assert review.judge is None
    assert review.structural is None
    assert review.detailed is not None
    assert review.final_verdict == "accepted"


def test_pipeline_rejects_invalid_depth(adapter: KbAdapter) -> None:
    verifier = ProofVerifier(backend=MockBackend())
    with pytest.raises(ValueError, match="depth"):
        verifier.run("algebra.lagrange", adapter, depth="bogus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Project-rules wiring (uses base role name = "proof-verifier")
# ---------------------------------------------------------------------------
def test_run_picks_up_proof_verifier_project_rules(adapter: KbAdapter) -> None:
    adapter.rules_dir.mkdir(parents=True, exist_ok=True)
    (adapter.rules_dir / "proof-verifier.md").write_text(
        "- All inequality proofs must state whether strict or non-strict.\n"
    )
    backend = MockBackend(canned_response=_judge_response("easy", verdict="accepted"))
    verifier = ProofVerifier(backend=backend)
    verifier.run_judge("algebra.lagrange", adapter)
    prompt = backend.last_call["prompt"]
    assert "Additional project rules" in prompt
    assert "inequality proofs" in prompt


# ---------------------------------------------------------------------------
# Per-stage backend routing
# ---------------------------------------------------------------------------
def test_backend_per_stage_routes_correctly(adapter: KbAdapter) -> None:
    judge_mock = MockBackend(name="judge-backend",
                             canned_response=_judge_response("hard"))
    structural_mock = MockBackend(name="structural-backend",
                                  canned_response=_structural_response("pass"))
    detailed_mock = MockBackend(name="detailed-backend",
                                canned_response=_detailed_response("accepted"))
    verifier = ProofVerifier(
        backend=judge_mock,  # default fallback (unused if all stages mapped)
        backend_per_stage={
            "judge": judge_mock,
            "structural": structural_mock,
            "detailed": detailed_mock,
        },
    )
    review = verifier.run("algebra.lagrange", adapter, depth="auto")
    assert review.final_verdict == "accepted"
    assert judge_mock.call_count == 1
    assert structural_mock.call_count == 1
    assert detailed_mock.call_count == 1


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------
def test_pipeline_surfaces_parse_error(adapter: KbAdapter) -> None:
    backend = MockBackend(canned_response="not json at all")
    verifier = ProofVerifier(backend=backend)
    with pytest.raises(ProofReviewParseError):
        verifier.run("algebra.lagrange", adapter, depth="auto")


# ---------------------------------------------------------------------------
# Test helper: backend that emits a sequence of responses
# ---------------------------------------------------------------------------
class _SequencedBackend(MockBackend):
    """MockBackend variant that pops a new canned response per .run() call."""

    def __init__(self, responses, **kw):
        super().__init__(**kw)
        self._responses = list(responses)

    def run(self, **kwargs):
        if self._responses:
            self.canned_response = self._responses.pop(0)
        return super().run(**kwargs)
