"""M12 / Phase II-B — induced-orbit toy stuck-state regression.

The induced-orbit runs that motivated Phase II were repeatedly repairing the
same rejected target and expanding broad algebraic-geometry background. This
test builds that shape directly in a temporary KB: a theorem at pass_count=-1
with repair_count above the automatic budget.
"""

from __future__ import annotations

from pathlib import Path

from common.kb.hashing import statement_hash, verification_hash
from common.kb.kuzu_backend import KuzuBackend
from common.kb.types import Node, NodeKind
from coordinator.heartbeat import IDLE_USER_BLOCKED
from coordinator.main import _KBSnapshot, _decide_idle_reason, _search_branch_stuck_targets
from coordinator.precheck import CandidateInput


def test_induced_orbit_toy_stuck_branch_is_not_dispatched_again(tmp_path: Path) -> None:
    (tmp_path / "knowledge_base").mkdir(parents=True)
    kb = KuzuBackend(tmp_path / "knowledge_base" / "dag.kz")
    try:
        statement = (
            "For the induced-orbit toy problem, the maximal real induced "
            "orbits are exactly the signed diagrams returned by the "
            "problem-specific algorithm."
        )
        proof = (
            "Rejected route: reduce maximality to broad open dense closure "
            "facts about varieties and generic algebraic geometry."
        )
        sh = statement_hash(
            label="thm:induced_orbit_toy",
            kind=NodeKind.THEOREM.value,
            statement=statement,
            depends_on=(),
        )
        vh = verification_hash(statement_hash_hex=sh, proof=proof)
        kb.create_node(
            Node(
                label="thm:induced_orbit_toy",
                kind=NodeKind.THEOREM,
                statement=statement,
                proof=proof,
                remark="toy regression for the induced-orbit dead-end",
                source_note="",
                pass_count=-1,
                repair_count=3,
                statement_hash=sh,
                verification_hash=vh,
                verification_report=(
                    "Same failure signature: generic open dense / closure "
                    "lemma does not prove the signed-diagram algorithm."
                ),
                repair_hint=(
                    "[verifier]\nStop expanding basic algebraic geometry; "
                    "spawn a problem-specific sibling branch."
                ),
            )
        )

        [row] = kb.coordinator_candidate_rows()
        cand = CandidateInput(
            target=row["target"],
            target_kind=row["target_kind"],
            statement=row["statement"],
            proof=row["proof"],
            statement_hash=row["statement_hash"],
            verification_hash=row["verification_hash"],
            pass_count=row["pass_count"],
            repair_count=row["repair_count"],
            repair_hint=row["repair_hint"],
            verification_report=row["verification_report"],
            dep_statement_hashes={},
            dep_pass_counts={},
            last_rejected_verification_hash=row["verification_hash"],
            introduced_by_actor=row["introduced_by_actor"],
        )
    finally:
        kb.close()

    assert cand.target == "thm:induced_orbit_toy"
    assert cand.pass_count == -1
    assert cand.repair_count == 3

    candidates = [cand]
    assert _search_branch_stuck_targets(candidates) == [
        {
            "kind": "search_branch_stuck",
            "target": "thm:induced_orbit_toy",
            "trigger": "repair_budget_exhausted",
            "reason": "max_automatic_repairs",
            "count": 3,
            "message": (
                "search branch stuck on thm:induced_orbit_toy: repair_count=3; "
                "spawn a sibling branch or add a strategy hint"
            ),
        }
    ]
    code, detail = _decide_idle_reason(
        _KBSnapshot(candidates=candidates),
        desired_pass_count=3,
        in_flight=0,
        dispatched_gen=0,
        dispatched_ver=0,
    )
    assert code == IDLE_USER_BLOCKED
    assert "search branches exhausted automatic repair budget" in detail


def test_induced_orbit_toy_generic_background_helper_is_guarded(tmp_path: Path) -> None:
    (tmp_path / "knowledge_base").mkdir(parents=True)
    kb = KuzuBackend(tmp_path / "knowledge_base" / "dag.kz")
    try:
        statement = (
            "Let K be a field, let X be a smooth K-variety of dimension n, "
            "and let x be a K-rational point. Then X admits an etale morphism "
            "to affine n-space near x."
        )
        proof = "Use the Jacobian criterion to choose local coordinates."
        sh = statement_hash(
            label="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
            kind=NodeKind.LEMMA.value,
            statement=statement,
            depends_on=(),
        )
        vh = verification_hash(statement_hash_hex=sh, proof=proof)
        kb.create_node(
            Node(
                label="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
                kind=NodeKind.LEMMA,
                statement=statement,
                proof=proof,
                remark="generic AG helper from induced-orbit toy run",
                source_note="",
                pass_count=-1,
                repair_count=1,
                statement_hash=sh,
                verification_hash=vh,
                verification_report=(
                    "The proof relies on uncited Jacobian criterion and etale "
                    "local model facts."
                ),
                repair_hint="Provide explicit citations or avoid this background detour.",
                introduced_by_actor="generator:codex-default",
            )
        )

        [row] = kb.coordinator_candidate_rows()
        cand = CandidateInput(
            target=row["target"],
            target_kind=row["target_kind"],
            statement=row["statement"],
            proof=row["proof"],
            statement_hash=row["statement_hash"],
            verification_hash=row["verification_hash"],
            pass_count=row["pass_count"],
            repair_count=row["repair_count"],
            repair_hint=row["repair_hint"],
            verification_report=row["verification_report"],
            dep_statement_hashes={},
            dep_pass_counts={},
            last_rejected_verification_hash=row["verification_hash"],
            introduced_by_actor=row["introduced_by_actor"],
        )
    finally:
        kb.close()

    [item] = _search_branch_stuck_targets([cand])
    assert item["kind"] == "generic_background_stuck"
    assert item["target"] == "lem:smooth_k_variety_rational_point_has_etale_affine_space_chart"
    code, detail = _decide_idle_reason(
        _KBSnapshot(candidates=[cand]),
        desired_pass_count=3,
        in_flight=0,
        dispatched_gen=0,
        dispatched_ver=0,
    )
    assert code == IDLE_USER_BLOCKED
    assert "background expansion guard" in detail
