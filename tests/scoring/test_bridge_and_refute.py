"""bridge.py + refute.py — three-stage audit gates."""

from __future__ import annotations

from rethlas_scoring.bridge import (
    BridgeAudit,
    EquivalenceJudgement,
)
from rethlas_scoring.data import ScoredNode
from rethlas_scoring.refute import (
    RefuteVerdict,
    RefuteVerdictKind,
    StubRefuteTask,
)


def _node(node_id: str, emb: tuple[float, ...]) -> ScoredNode:
    return ScoredNode(id=node_id, claim_text=node_id, embedding=emb)


def _judge_yes(_a: str, _b: str) -> EquivalenceJudgement:
    return EquivalenceJudgement.YES_STRICT


def _judge_no(_a: str, _b: str) -> EquivalenceJudgement:
    return EquivalenceJudgement.NO


_REFUTE_OK = StubRefuteTask(
    verdict=RefuteVerdict(verdict_kind=RefuteVerdictKind.NO_ISSUE, severity=0.0)
)
_REFUTE_BAD = StubRefuteTask(
    verdict=RefuteVerdict(verdict_kind=RefuteVerdictKind.COUNTEREXAMPLE, severity=0.9)
)


def test_bridge_rejects_low_cosine() -> None:
    audit = BridgeAudit(judge=_judge_yes, refuter=_REFUTE_OK)
    rep = audit.audit(_node("u", (1.0, 0.0)), _node("v", (0.0, 1.0)))
    assert not rep.accepted
    assert "cosine" in rep.reason


def test_bridge_rejects_when_judge_says_no() -> None:
    audit = BridgeAudit(judge=_judge_no, refuter=_REFUTE_OK)
    rep = audit.audit(_node("u", (1.0, 0.0, 0.0)), _node("v", (1.0, 0.0, 0.0)))
    assert not rep.accepted
    assert "judge" in rep.reason


def test_bridge_rejects_when_refute_finds_counterexample() -> None:
    audit = BridgeAudit(judge=_judge_yes, refuter=_REFUTE_BAD)
    rep = audit.audit(_node("u", (1.0, 0.0)), _node("v", (1.0, 0.0)))
    assert not rep.accepted
    assert "counterexample" in rep.reason
    assert rep.refute_verdict is not None
    assert rep.refute_verdict.found_counterexample


def test_bridge_accepts_when_all_three_stages_pass() -> None:
    audit = BridgeAudit(judge=_judge_yes, refuter=_REFUTE_OK)
    rep = audit.audit(_node("u", (1.0, 0.0)), _node("v", (1.0, 0.0)))
    assert rep.accepted
    assert rep.requires_endgame_recheck is True


def test_refute_verdict_clamps_severity() -> None:
    rv = RefuteVerdict(verdict_kind=RefuteVerdictKind.FRAGILE, severity=2.5)
    assert rv.severity == 1.0
    rv2 = RefuteVerdict(verdict_kind=RefuteVerdictKind.NO_ISSUE, severity=-1.0)
    assert rv2.severity == 0.0
