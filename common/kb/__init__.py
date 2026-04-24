"""KB data types + pure hash helpers.

:mod:`common.kb.types` — pure dataclasses (Node, Event, AppliedEvent, …).
:mod:`common.kb.hashing` — deterministic hashes.

Workers (generator/verifier ``role.py``) are **allowed** to import these
since they are all pure Python with no Kuzu dependency. The §4.1 Kuzu-free
invariant only forbids importing the (future) ``common.kb.store`` module.
"""

from common.kb.hashing import (
    DepRef,
    canonical_json,
    statement_hash,
    verification_hash,
)
from common.kb.types import (
    AXIOM_KINDS,
    KIND_PREFIX,
    PROOF_REQUIRING_KINDS,
    AppliedEvent,
    ApplyOutcome,
    Event,
    Node,
    NodeKind,
    StagedBatch,
    StagedBatchNode,
)

__all__ = [
    "AXIOM_KINDS",
    "AppliedEvent",
    "ApplyOutcome",
    "DepRef",
    "Event",
    "KIND_PREFIX",
    "Node",
    "NodeKind",
    "PROOF_REQUIRING_KINDS",
    "StagedBatch",
    "StagedBatchNode",
    "canonical_json",
    "statement_hash",
    "verification_hash",
]
