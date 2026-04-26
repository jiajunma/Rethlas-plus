"""Pre-dispatch validation gate (ARCHITECTURE §5.5.2).

Coordinator runs every dispatch candidate through this module BEFORE
writing a job file. Any failure here is logged to
``runtime/logs/supervise.log`` and the candidate is skipped on this
tick — no job file, no Codex, no event.

The checks (§5.5.2 table):

| Check | Generator | Verifier |
| --- | --- | --- |
| Target node exists at ``pass_count`` matching the pool | ✓ | ✓ |
| Target's hash hasn't drifted since last tick | ✓ | ✓ |
| All explicit deps of target are at ``pass_count >= 1`` | ✓ | ✓ |
| No other in-flight job already targets the same label | ✓ | ✓ |
| (repair only) ``H_rejected`` recorded and current | — | ✓-irrelevant |

This module is **read-only** — it doesn't write the job file. The
caller composes the JobRecord from the same context dict on success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class PrecheckFailure:
    target: str
    kind: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Everything coordinator passes to the wrapper job file (§6.7.1 step 1)."""

    target: str
    target_kind: str
    statement: str
    proof: str
    statement_hash: str
    verification_hash: str
    repair_count: int
    repair_hint: str
    verification_report: str
    dep_statement_hashes: dict[str, str]
    h_rejected: str  # populated in repair mode only


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """Per-candidate KB snapshot used for precheck."""

    target: str
    target_kind: str
    statement: str
    proof: str
    statement_hash: str
    verification_hash: str
    pass_count: int
    repair_count: int
    repair_hint: str
    verification_report: str
    dep_statement_hashes: dict[str, str]
    dep_pass_counts: dict[str, int]
    last_rejected_verification_hash: str = ""

    @property
    def deps_ready(self) -> bool:
        return all(
            self.dep_pass_counts.get(dep, -1) >= 1 and bool(self.dep_statement_hashes.get(dep))
            for dep in self.dep_statement_hashes
        )

    @property
    def verifier_deps_strictly_ahead(self) -> bool:
        return all(self.dep_pass_counts.get(dep, -1) > self.pass_count for dep in self.dep_statement_hashes)


def precheck_generator(
    cand: CandidateInput,
    *,
    in_flight_targets: Iterable[str],
    expected_hash_for_drift_check: str | None = None,
) -> tuple[DispatchContext | None, PrecheckFailure | None]:
    """Validate ``cand`` for a generator dispatch.

    Returns ``(context, None)`` when the candidate passes; otherwise
    ``(None, failure)`` with a structured reason.
    """
    if cand.pass_count != -1:
        return None, _fail(cand, "generator", "pool_mismatch", f"pass_count={cand.pass_count} (need -1)")
    if cand.target in in_flight_targets:
        return None, _fail(cand, "generator", "in_flight", "another job already targets this label")
    if not cand.deps_ready:
        missing = [k for k, v in cand.dep_statement_hashes.items() if not v]
        return None, _fail(cand, "generator", "deps_not_ready", f"missing deps: {missing}")
    if (
        expected_hash_for_drift_check is not None
        and expected_hash_for_drift_check != cand.verification_hash
    ):
        return None, _fail(
            cand, "generator", "hash_drift",
            f"expected={expected_hash_for_drift_check[:12]} actual={cand.verification_hash[:12]}",
        )

    mode = "fresh" if cand.repair_count == 0 else "repair"
    h_rejected = cand.last_rejected_verification_hash if mode == "repair" else ""
    if mode == "repair" and not h_rejected:
        return None, _fail(
            cand, "generator", "missing_h_rejected",
            "repair mode but no rejected verification_hash recorded",
        )
    if mode == "repair" and h_rejected != cand.verification_hash:
        return None, _fail(
            cand, "generator", "h_rejected_stale",
            f"H_rejected={h_rejected[:12]} != current vh={cand.verification_hash[:12]}",
        )
    ctx = DispatchContext(
        target=cand.target,
        target_kind=cand.target_kind,
        statement=cand.statement,
        proof=cand.proof,
        statement_hash=cand.statement_hash,
        verification_hash=cand.verification_hash,
        repair_count=cand.repair_count,
        repair_hint=cand.repair_hint,
        verification_report=cand.verification_report,
        dep_statement_hashes=dict(cand.dep_statement_hashes),
        h_rejected=h_rejected,
    )
    return ctx, None


def precheck_verifier(
    cand: CandidateInput,
    *,
    in_flight_targets: Iterable[str],
) -> tuple[DispatchContext | None, PrecheckFailure | None]:
    """Validate ``cand`` for a verifier dispatch.

    Verifier wants nodes with ``pass_count >= 0`` AND ``pass_count <
    desired`` — but the desired threshold is tracked outside this
    function (the caller filters before calling). Here we just check
    that the candidate is in the verifier band and not already in flight.
    """
    if cand.pass_count < 0:
        return None, _fail(cand, "verifier", "pool_mismatch", f"pass_count={cand.pass_count} (need >= 0)")
    if cand.target in in_flight_targets:
        return None, _fail(cand, "verifier", "in_flight", "another job already targets this label")
    if not cand.deps_ready:
        return None, _fail(cand, "verifier", "deps_not_ready", "deps not all at pass_count>=1")
    if not cand.verifier_deps_strictly_ahead:
        lagging = [
            f"{dep}={cand.dep_pass_counts.get(dep, -1)}"
            for dep in cand.dep_statement_hashes
            if cand.dep_pass_counts.get(dep, -1) <= cand.pass_count
        ]
        return None, _fail(
            cand,
            "verifier",
            "deps_not_strictly_ahead",
            "deps not strictly ahead: " + ", ".join(lagging),
        )
    ctx = DispatchContext(
        target=cand.target,
        target_kind=cand.target_kind,
        statement=cand.statement,
        proof=cand.proof,
        statement_hash=cand.statement_hash,
        verification_hash=cand.verification_hash,
        repair_count=cand.repair_count,
        repair_hint=cand.repair_hint,
        verification_report=cand.verification_report,
        dep_statement_hashes=dict(cand.dep_statement_hashes),
        h_rejected="",
    )
    return ctx, None


def _fail(cand: CandidateInput, kind: str, reason: str, detail: str) -> PrecheckFailure:
    return PrecheckFailure(target=cand.target, kind=kind, reason=reason, detail=detail)


__all__ = [
    "CandidateInput",
    "DispatchContext",
    "PrecheckFailure",
    "precheck_generator",
    "precheck_verifier",
]
