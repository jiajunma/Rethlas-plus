"""L5 node-state policy — DETERMINISTIC state machine.

Implements ``docs/SCORING_SCHEDULING.md §7`` verbatim:

- ``classify(evidence)`` decides the node's state from the algebra of
  collected evidence — **no probabilities, no thresholds on inferred
  rates**. The user's "证明对就是对错就是错" principle (proof correctness
  is binary, not a probability) is enforced by construction: every state
  transition is grounded in a count of concrete evidence items.
- ``next_action(state, evidence, budget)`` returns the next escalation
  step: ``DEFAULT_VERIFY`` → ``REFUTE`` → ``STRONG_VERIFY`` →
  ``USER_BLOCKED``. Once the ladder is exhausted, the node hands off to
  a human; the system never makes a probabilistic call on its own.

Pure stdlib. No coupling to ``rethlas_scoring.calibration`` or VOI —
those concerns live one layer above (L4: which node first; this layer:
what to do for it).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


# ---------------------------------------------------------------------------
# Enums.
# ---------------------------------------------------------------------------
class EvidenceKind(str, Enum):
    """Which verifier role produced the evidence."""

    DEFAULT = "default"  # production LLM verifier (current `verifier/role.py`)
    REFUTE = "refute"  # dual task searching for counterexamples
    STRONG = "strong"  # higher-budget LLM (e.g. Opus thinking)


class VerdictKind(str, Enum):
    """Decoder-level verdict from one verifier call."""

    OK = "ok"
    GAP = "gap"
    CRITICAL = "critical"
    ABSTAIN = "abstain"


class NodeState(str, Enum):
    """L5 state of a single node, derived from its evidence list."""

    PENDING = "pending"
    DISAGREEMENT = "disagreement"
    VERIFIED = "verified"
    REFUTED = "refuted"
    USER_BLOCKED = "user_blocked"


class Action(str, Enum):
    """Next dispatch action for a node."""

    NONE = "none"  # terminal; do not dispatch
    DEFAULT_VERIFY = "default_verify"
    REFUTE = "refute"
    STRONG_VERIFY = "strong_verify"
    USER_BLOCKED = "user_blocked"  # ladder exhausted; hand to human


# ---------------------------------------------------------------------------
# Dataclasses.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Evidence:
    """One row in a node's evidence ledger.

    ``ts_iso`` follows the project-wide ISO 8601 ``...Z`` convention
    (mirrors ``common.kb.types.Event.ts``). ``counterexample`` is only
    populated when ``kind == REFUTE`` and the refute task found a concrete
    witness; ``classify`` treats any non-empty value as a hard refutation.
    """

    kind: EvidenceKind
    worker_id: str
    verdict: VerdictKind
    ts_iso: str
    counterexample: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyBudget:
    """Per-node escalation caps (DESIGN §7.3).

    Defaults reflect ``docs/SCORING_DESIGN.md §11`` parameter table.
    """

    max_refute: int = 1  # one refute attempt per disagreement
    max_strong: int = 1  # one strong-model fallback before user_blocked


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def pass_count_from_evidence(evidence: Iterable[Evidence]) -> int:
    """``Node.pass_count`` per §7.1: distinct workers whose default-kind
    verdict was OK.

    Refute / strong verdicts don't count toward ``pass_count`` even when
    OK — they live on the side ladder. Worker IDs are deduplicated so
    a single worker re-running cannot inflate the count.
    """

    seen: set[str] = set()
    for e in evidence:
        if (
            e.kind is EvidenceKind.DEFAULT
            and e.verdict is VerdictKind.OK
            and e.worker_id not in seen
        ):
            seen.add(e.worker_id)
    return len(seen)


# ---------------------------------------------------------------------------
# State machine — pure functions.
# ---------------------------------------------------------------------------
def classify(
    evidence: Iterable[Evidence], *, desired_pass: int = 3
) -> NodeState:
    """Return the node's current state given its accumulated evidence.

    Decision order (first matching rule wins):

    1. **REFUTED** — any REFUTE evidence with a concrete counterexample.
    2. **VERIFIED** — at least one STRONG OK and zero STRONG CRITICAL.
    3. **USER_BLOCKED** — at least one STRONG CRITICAL with no
       counterexample on file (strong model says wrong, no witness yet).
    4. **VERIFIED** — DEFAULT OK count ≥ ``desired_pass`` (deduplicated by
       worker via ``pass_count_from_evidence``) and no DEFAULT CRITICAL.
    5. **DISAGREEMENT** — any mix of DEFAULT OK and DEFAULT CRITICAL
       (same node verified by ≥2 default workers with conflicting
       verdicts).
    6. **PENDING** — none of the above; collect more evidence.

    See ``docs/SCORING_SCHEDULING.md §7.2`` for the rationale and §7.4
    for why this is consistent with the binary-truth principle.
    """

    ev_list = list(evidence)

    # 1. Hard refutation by counterexample.
    if any(
        e.kind is EvidenceKind.REFUTE and e.counterexample
        for e in ev_list
    ):
        return NodeState.REFUTED

    # 2/3. Strong-model evidence is decisive (one vote).
    n_strong_ok = sum(
        1
        for e in ev_list
        if e.kind is EvidenceKind.STRONG and e.verdict is VerdictKind.OK
    )
    n_strong_crit = sum(
        1
        for e in ev_list
        if e.kind is EvidenceKind.STRONG and e.verdict is VerdictKind.CRITICAL
    )
    if n_strong_ok >= 1 and n_strong_crit == 0:
        return NodeState.VERIFIED
    if n_strong_crit >= 1:
        return NodeState.USER_BLOCKED

    # 4/5. Default-verifier evidence — needs ≥desired_pass agreement.
    n_default_ok = pass_count_from_evidence(ev_list)
    n_default_crit = sum(
        1
        for e in ev_list
        if e.kind is EvidenceKind.DEFAULT and e.verdict is VerdictKind.CRITICAL
    )
    if n_default_ok >= desired_pass and n_default_crit == 0:
        return NodeState.VERIFIED
    if n_default_ok >= 1 and n_default_crit >= 1:
        return NodeState.DISAGREEMENT

    return NodeState.PENDING


def next_action(
    state: NodeState,
    evidence: Iterable[Evidence],
    budget: PolicyBudget = PolicyBudget(),
) -> Action:
    """Return the next dispatch action for a node in the given state.

    Terminal states (``VERIFIED``, ``REFUTED``, ``USER_BLOCKED``) return
    ``NONE`` — the dispatcher should not pick them. ``PENDING`` always
    yields ``DEFAULT_VERIFY`` (collect another LLM pass).
    ``DISAGREEMENT`` walks the escalation ladder:
    ``REFUTE`` → ``STRONG_VERIFY`` → ``USER_BLOCKED``.
    """

    if state in (NodeState.VERIFIED, NodeState.REFUTED, NodeState.USER_BLOCKED):
        return Action.NONE

    ev_list = list(evidence)

    if state is NodeState.DISAGREEMENT:
        n_refute = sum(1 for e in ev_list if e.kind is EvidenceKind.REFUTE)
        n_strong = sum(1 for e in ev_list if e.kind is EvidenceKind.STRONG)
        if n_refute < budget.max_refute:
            return Action.REFUTE
        if n_strong < budget.max_strong:
            return Action.STRONG_VERIFY
        return Action.USER_BLOCKED

    # state is PENDING
    return Action.DEFAULT_VERIFY


__all__ = [
    "Action",
    "Evidence",
    "EvidenceKind",
    "NodeState",
    "PolicyBudget",
    "VerdictKind",
    "classify",
    "next_action",
    "pass_count_from_evidence",
]
