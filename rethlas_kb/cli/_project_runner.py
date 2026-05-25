"""Batch-over-project plumbing for the v1.3 ``--project`` flag (issue #15).

Each workflow command (``verify-stmt``, ``verify-proof``, ``fill-gap``,
``hunt-counterexample``, ``audit-source``) accepts an optional
``--project <id>``. When supplied, the command:

1. Loads the project manifest from
   ``<blueprint>/.rethlas-kb/projects/<id>.yml``
2. Computes the closure (transitive ``uses:`` from goal_nodes)
3. Filters the closure to nodes the agent applies to
   (per :func:`applies_to`)
4. Runs the agent on each filtered node sequentially
5. Aggregates the per-node outcomes into a :class:`BatchOutcome`

This module owns the iteration shape + the applicability matrix; each
workflow plugs its own per-node "run + persist + extract verdict"
logic via a callback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator

from rethlas_kb.project import Project, closure_distances

if TYPE_CHECKING:
    from rethlas_kb.adapter import KbAdapter
    from tools.knowledge.models import Node


# ---------------------------------------------------------------------------
# Applicability matrix
# ---------------------------------------------------------------------------
_STATEMENT_KINDS = frozenset({"lemma", "proposition", "theorem", "external-theorem"})


def applies_to(agent_role: str, node: "Node") -> bool:
    """Return True iff the agent should be run on this node.

    The matrix codifies which agents care about which kinds + statuses.
    Skipping vs running here is just an efficiency hint — the agent
    itself will produce a sensible verdict either way; we just avoid
    burning tokens on irrelevant nodes.

    Rules:

    - ``statement-verifier``:    staged-status nodes (anything we might
                                  admit) except meta-kinds (task /
                                  proof-plan)
    - ``proof-verifier``:        staged statement-kinds (lemma /
                                  proposition / theorem / external-theorem)
    - ``proof-gap-filler``:      staged statement-kinds whose proof
                                  hasn't been accepted yet
    - ``counterexample-hunter``: staged statement-kinds (something to
                                  potentially refute)
    - ``audit-source``:          any external-theorem (regardless of
                                  status — alignment can be re-checked)
    """
    from tools.knowledge.models import ADMITTED_STATUSES, STAGED_STATUSES

    kind = node.kind or ""
    status = node.status or ""

    if agent_role == "statement-verifier":
        if kind in ("task", "proof-plan"):
            return False
        return status in STAGED_STATUSES

    if agent_role in ("proof-verifier", "counterexample-hunter"):
        return status in STAGED_STATUSES and kind in _STATEMENT_KINDS

    if agent_role == "proof-gap-filler":
        if status not in STAGED_STATUSES or kind not in _STATEMENT_KINDS:
            return False
        # If the node has an accepted proof verdict already, skip — the
        # gap-filler has nothing to repair.
        v = getattr(node, "verification", None)
        if v is not None and getattr(v, "proof", None) == "accepted":
            return False
        return True

    if agent_role == "source-claim-verifier":
        return kind == "external-theorem"

    # Unknown role — be permissive (the caller chose to invoke this
    # agent, so apply it everywhere). The error-mode is the agent
    # producing a "not applicable" review, not a silent skip.
    return True


# ---------------------------------------------------------------------------
# Batch iteration
# ---------------------------------------------------------------------------
def iter_applicable_nodes(
    project: Project,
    adapter: "KbAdapter",
    agent_role: str,
) -> Iterator["Node"]:
    """Yield closure-member nodes that the named agent applies to.

    Sorted by distance-from-goal (closer = higher priority), then by
    node id alphabetical for stable output. Missing closure members
    (referenced by ``uses:`` but absent from the KB) are skipped — they
    aren't actionable for any agent that takes a node as input.
    """
    dist = closure_distances(project, adapter)
    pairs: list[tuple[int, str, "Node"]] = []
    for nid, d in dist.items():
        try:
            node = adapter.read_node(nid)
        except KeyError:
            continue
        if applies_to(agent_role, node):
            pairs.append((d, nid, node))
    pairs.sort(key=lambda t: (t[0], t[1]))
    for _, _, node in pairs:
        yield node


# ---------------------------------------------------------------------------
# Batch outcome aggregation
# ---------------------------------------------------------------------------
@dataclass
class BatchItem:
    """One per-node outcome in a batch run."""

    node_id: str
    outcome: str             # "accepted" | "flagged" | "crashed" | "skipped"
    summary: str = ""        # one-line description (decision + rationale tail)
    review_path: str | None = None  # path of the persisted review, when written
    error: str | None = None  # only set when outcome == "crashed"


@dataclass
class BatchOutcome:
    """Aggregate of one ``--project`` batch invocation."""

    project_id: str
    agent_role: str
    closure_count: int
    applicable_count: int
    items: list[BatchItem] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return sum(1 for i in self.items if i.outcome == "accepted")

    @property
    def flagged_count(self) -> int:
        return sum(1 for i in self.items if i.outcome == "flagged")

    @property
    def crashed_count(self) -> int:
        return sum(1 for i in self.items if i.outcome == "crashed")

    @property
    def all_accepted(self) -> bool:
        return self.crashed_count == 0 and self.flagged_count == 0


# ---------------------------------------------------------------------------
# Driver — workflows pass in a per-node callback
# ---------------------------------------------------------------------------
PerNodeRunner = Callable[["Node"], BatchItem]


def run_batch(
    project: Project,
    adapter: "KbAdapter",
    agent_role: str,
    per_node: PerNodeRunner,
) -> BatchOutcome:
    """Run ``per_node`` on every applicable closure member; aggregate outcomes.

    The callback owns persistence + verdict extraction — this driver just
    iterates, catches unexpected exceptions per-node so one crash doesn't
    abort the rest, and tallies the result.
    """
    closure_ids = {n for n in closure_distances(project, adapter)}
    applicable_nodes = list(iter_applicable_nodes(project, adapter, agent_role))
    outcome = BatchOutcome(
        project_id=project.id,
        agent_role=agent_role,
        closure_count=len(closure_ids),
        applicable_count=len(applicable_nodes),
    )
    for node in applicable_nodes:
        try:
            item = per_node(node)
        except Exception as exc:  # noqa: BLE001 — we intentionally catch all
            item = BatchItem(
                node_id=node.id, outcome="crashed",
                summary=f"{type(exc).__name__}: {exc}",
                error=str(exc),
            )
        outcome.items.append(item)
    return outcome


__all__ = [
    "BatchItem",
    "BatchOutcome",
    "PerNodeRunner",
    "applies_to",
    "iter_applicable_nodes",
    "run_batch",
]
