"""Foundational dataclasses for rethlas_scoring.

Designed so that ``ScoredNode`` can wrap or shadow ``common.kb.types.Node``:
the existing ``Node`` is ``frozen=True, slots=True`` and lacks
``posterior_p``/``embedding``/``refute_severity``. The scoring layer keeps
those extension fields in its own immutable container, indexed by label
(``Node.label`` ≡ ``ScoredNode.id``).

The dataclasses here carry **no I/O** and **no Kuzu coupling** — that
matches the §4.1 Kuzu-free invariant of the rest of the project's worker
side modules.

See also ``docs/SCORING_DESIGN.md §2`` for the data-model spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping


# ---------------------------------------------------------------------------
# Default parameters (DESIGN §12).
# ---------------------------------------------------------------------------
class _Defaults:
    """Centralised parameter defaults referenced by all submodules.

    Mirrors the table in ``docs/SCORING_DESIGN.md §12``. Keeping them in
    one place lets tests override them without monkey-patching individual
    modules.
    """

    embedding_threshold: float = 0.65
    cluster_decay: float = 0.5
    ensemble_size: int = 3
    voi_mc_samples: int = 200
    isotonic_min_samples: int = 30
    thompson_temperature_start: float = 1.0
    thompson_temperature_end: float = 0.2
    certify_epsilon: float = 0.01
    refute_severity_threshold: float = 0.4
    bridge_cosine_threshold: float = 0.85
    # Beta(α, β) prior for verifier ROC buckets (DESIGN §3.1).
    roc_prior_alpha: float = 1.0
    roc_prior_beta: float = 1.0
    # Floor / ceiling on probabilities so log/division never blow up.
    prob_eps: float = 1e-9


DEFAULTS = _Defaults()


# ---------------------------------------------------------------------------
# Status enum (DESIGN §2).
# ---------------------------------------------------------------------------
class NodeStatus(str, Enum):
    OPEN = "open"
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REFUTED = "refuted"
    SPECULATIVE = "speculative"


# ---------------------------------------------------------------------------
# Verifier observation (one ensemble call) — DESIGN §3 + §7.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VerifierObservation:
    """One verifier call's outcome, plus optional ground-truth feedback.

    ``ts_iso`` follows ISO 8601 with explicit ``Z`` suffix — same
    convention as ``common.kb.types.Event.ts``.
    """

    worker_id: str
    predicted_ok: bool
    confidence: float
    ground_truth_ok: bool | None
    ts_iso: str
    bucket_id: int = 0  # difficulty bucket assigned by VerifierROC.bucket()


# ---------------------------------------------------------------------------
# ScoredNode — extension fields the scoring layer needs.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ScoredNode:
    """Scoring-layer view of a proof DAG node.

    ``id`` is the node label (matches ``common.kb.types.Node.label``).
    The actual claim text and embedding are kept here rather than in the
    KB ``Node`` to keep the KB record minimal until the embedding pipeline
    lands (Phase C — see ``docs/SCORING_AUDIT.md §4.3``).
    """

    id: str
    claim_text: str
    status: NodeStatus = NodeStatus.OPEN
    prior_p: float = 0.5
    posterior_p: float = 0.5
    embedding: tuple[float, ...] = ()
    depends_on: tuple[str, ...] = ()
    speculative_load: float = 0.0
    refute_severity: float = 0.0
    hardness: float = 0.5  # LLM-self-rated [0,1] for difficulty bucketing
    verifier_history: tuple[VerifierObservation, ...] = ()
    # Estimated wall-clock + token cost of *re-verifying* this node.
    # Lower bound to avoid /0 in ``priority = info / cost``.
    cost: float = 1.0

    def with_posterior(self, p: float) -> "ScoredNode":
        """Return a new ScoredNode with clamped posterior (I2)."""
        clamped = max(0.0, min(1.0, p))
        return replace(self, posterior_p=clamped)

    def with_status(self, status: NodeStatus) -> "ScoredNode":
        return replace(self, status=status)

    def with_observation(self, obs: VerifierObservation) -> "ScoredNode":
        return replace(self, verifier_history=self.verifier_history + (obs,))

    def with_refute_severity(self, sev: float) -> "ScoredNode":
        clamped = max(0.0, min(1.0, sev))
        return replace(self, refute_severity=clamped)


# ---------------------------------------------------------------------------
# ProofGraph — adjacency + node table.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ProofGraph:
    """Immutable DAG view: id → ScoredNode plus child adjacency.

    ``children[u]`` lists every node v such that ``u in v.depends_on``.
    Constructed once via ``ProofGraph.build``; mutators return new graphs
    (immutability per common.coding-style.md).
    """

    nodes: Mapping[str, ScoredNode]
    children: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def build(cls, nodes: Mapping[str, ScoredNode]) -> "ProofGraph":
        children: dict[str, list[str]] = {nid: [] for nid in nodes}
        for child_id, node in nodes.items():
            for parent_id in node.depends_on:
                if parent_id in children:
                    children[parent_id].append(child_id)
        # Freeze to tuples for hashability + immutability.
        frozen_children = {k: tuple(sorted(v)) for k, v in children.items()}
        return cls(nodes=dict(nodes), children=frozen_children)

    # ---- queries ---------------------------------------------------------
    def get(self, node_id: str) -> ScoredNode:
        return self.nodes[node_id]

    def parents(self, node_id: str) -> tuple[str, ...]:
        return self.nodes[node_id].depends_on

    def descendants(self, node_id: str) -> set[str]:
        """Transitive closure of children (excludes ``node_id`` itself)."""
        seen: set[str] = set()
        stack = list(self.children.get(node_id, ()))
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            stack.extend(self.children.get(v, ()))
        return seen

    def ancestors(self, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.parents(node_id))
        while stack:
            v = stack.pop()
            if v in seen:
                continue
            seen.add(v)
            stack.extend(self.parents(v))
        return seen

    def relevance_cone(self, node_id: str) -> set[str]:
        """Nodes whose truth value affects ``Z``'s answer for ``node_id``.

        Per DESIGN §4.2 step "Z_i := ∧_{u in v's relevance cone} T_i(u)":
        the cone is the union of the node, its ancestors (everything it
        depends on), and its descendants (everything that uses it).
        """

        return {node_id} | self.ancestors(node_id) | self.descendants(node_id)

    # ---- mutators (immutable) -------------------------------------------
    def replace_node(self, node: ScoredNode) -> "ProofGraph":
        new_nodes = dict(self.nodes)
        new_nodes[node.id] = node
        # Only the node table changed; topology untouched.
        return ProofGraph(nodes=new_nodes, children=self.children)


__all__ = [
    "DEFAULTS",
    "NodeStatus",
    "ProofGraph",
    "ScoredNode",
    "VerifierObservation",
]
