"""Runtime dataclasses for the projected KB (ARCHITECTURE §5.2, §5.4).

These are **pure data containers** — no Kuzu dependency and no I/O. Callers
that need to move rows between layers (librarian apply, linter audit,
dashboard reader) construct these dataclasses from whatever backend they
talk to. Workers (generator / verifier) import only :mod:`common.events`
and :mod:`common.kb.types` + :mod:`common.kb.hashing`; they never import
:mod:`common.kb.store` (whenever that lands in M2) — that's the §4.1
Kuzu-free invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any


# ---------------------------------------------------------------------------
# Node kinds (ARCHITECTURE §5.1).
# ---------------------------------------------------------------------------
class NodeKind(str, Enum):
    DEFINITION = "definition"
    EXTERNAL_THEOREM = "external_theorem"
    LEMMA = "lemma"
    THEOREM = "theorem"
    PROPOSITION = "proposition"


PROOF_REQUIRING_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.LEMMA, NodeKind.THEOREM, NodeKind.PROPOSITION}
)
AXIOM_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.DEFINITION, NodeKind.EXTERNAL_THEOREM}
)

# Allowed label prefixes per kind (§3.5.2).
KIND_PREFIX: dict[NodeKind, str] = {
    NodeKind.DEFINITION: "def",
    NodeKind.EXTERNAL_THEOREM: "ext",
    NodeKind.LEMMA: "lem",
    NodeKind.THEOREM: "thm",
    NodeKind.PROPOSITION: "prop",
}

LABEL_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
PLACEHOLDER_LABELS: frozenset[str] = frozenset(
    {
        "thm:main",
        "thm:helper",
        "thm:placeholder",
        "lem:helper",
        "lem:key_step",
        "lem:main",
        "lem:placeholder",
        "prop:aux",
        "prop:claim1",
        "prop:helper",
        "prop:main",
        "def:object",
        "def:placeholder",
        "ext:placeholder",
    }
)


# ---------------------------------------------------------------------------
# Node (ARCHITECTURE §5.2).
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Node:
    label: str
    kind: NodeKind
    statement: str
    proof: str  # empty string for axioms
    remark: str
    source_note: str
    pass_count: int  # -1 / 0 / ≥ 1 per §5.4
    repair_count: int  # ≥ 0
    statement_hash: str  # hex
    verification_hash: str  # hex
    verification_report: str = ""
    repair_hint: str = ""
    depends_on: tuple[str, ...] = ()  # label list; empty by default
    # Provenance: actor that *first introduced* this label (kind:instance,
    # e.g. ``user:cli`` or ``generator:codex-default``). Set on creation,
    # preserved across revisions. Used by the verifier-reject router so
    # generator-introduced helper definitions can be repaired by the
    # generator instead of escalating to ``user_blocked``.
    introduced_by_actor: str = "user:cli"

    def initial_count(self) -> int:
        """§5.4 ``initial_count(kind, proof)``."""
        if self.kind in AXIOM_KINDS:
            return 0
        return 0 if self.proof else -1

    @property
    def introduced_by_generator(self) -> bool:
        """True iff the label was first introduced by a generator actor."""
        return self.introduced_by_actor.startswith("generator:")


# ---------------------------------------------------------------------------
# Event-facing dataclass (just the envelope; per-type payload lives elsewhere).
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    type: str
    actor: str
    ts: str  # full ISO with offset per §3.3
    payload: dict[str, Any]
    target: str | None = None
    cost: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, body: dict[str, Any]) -> "Event":
        return cls(
            event_id=body["event_id"],
            type=body["type"],
            actor=body["actor"],
            ts=body["ts"],
            payload=dict(body["payload"]),
            target=body.get("target"),
            cost=body.get("cost"),
        )


# ---------------------------------------------------------------------------
# AppliedEvent (ARCHITECTURE §5.2).
# ---------------------------------------------------------------------------
class ApplyOutcome(str, Enum):
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"


@dataclass(frozen=True, slots=True)
class AppliedEvent:
    event_id: str
    event_sha256: str  # raw-byte hash of the on-disk event file
    status: ApplyOutcome
    reason: str | None  # populated only when status == APPLY_FAILED
    detail: str | None  # human-readable detail matching §5.2 reason table
    applied_at: str  # UTC ISO 8601 Z (§2.4 trailer)
    # Pointers back to the node the event touched, when applicable.
    target_label: str | None = None

    @property
    def is_applied(self) -> bool:
        return self.status is ApplyOutcome.APPLIED


# ---------------------------------------------------------------------------
# Convenience: a "staged batch" the generator decoder assembles before
# publishing. Used by §6.2 — placed here so M6 doesn't have to invent a
# new dataclass.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StagedBatchNode:
    label: str
    kind: NodeKind
    statement: str
    proof: str
    remark: str
    source_note: str


@dataclass(frozen=True, slots=True)
class StagedBatch:
    target: str
    mode: str  # ``fresh`` or ``repair`` (§10.2.3)
    nodes: tuple[StagedBatchNode, ...] = field(default_factory=tuple)
