"""M12 / Phase II-B — stop repeated single-route repair spirals."""

from __future__ import annotations

from coordinator.heartbeat import IDLE_GEN_DEP_BLOCKED, IDLE_USER_BLOCKED
from coordinator.main import (
    _KBSnapshot,
    _PHASE2_MAX_AUTOMATIC_REPAIR_COUNT,
    _PHASE2_USER_BRANCH_OVERRIDE,
    _decide_idle_reason,
    _is_generic_background_stuck,
    _is_search_branch_stuck,
    _phase2_reroute_candidate,
    _phase2_reroute_parent_labels,
    _search_branch_stuck_targets,
)
from coordinator.precheck import CandidateInput


def _cand(**overrides) -> CandidateInput:
    base = dict(
        target="thm:goal",
        target_kind="theorem",
        statement="S",
        proof="bad proof",
        statement_hash="ab" * 32,
        verification_hash="cd" * 32,
        pass_count=-1,
        repair_count=_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT,
        repair_hint="[verifier]\nsame gap",
        verification_report="same gap",
        dep_statement_hashes={"def:x": "ef" * 32},
        dep_pass_counts={"def:x": 1},
        last_rejected_verification_hash="cd" * 32,
    )
    base.update(overrides)
    return CandidateInput(**base)


def test_second_repair_is_still_a_search_branch_attempt() -> None:
    cand = _cand(repair_count=_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT)

    assert not _is_search_branch_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_exhausted_branch_is_surfaced_and_no_longer_auto_dispatched() -> None:
    cand = _cand(repair_count=_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT + 1)

    assert _is_search_branch_stuck(cand)
    [item] = _search_branch_stuck_targets([cand])
    assert item["kind"] == "search_branch_stuck"
    assert item["trigger"] == "repair_budget_exhausted"
    assert item["reason"] == "max_automatic_repairs"
    assert item["target"] == "thm:goal"
    assert item["count"] == _PHASE2_MAX_AUTOMATIC_REPAIR_COUNT + 1
    assert "spawn a sibling branch" in item["message"]

    code, detail = _decide_idle_reason(
        _KBSnapshot(candidates=[cand]),
        desired_pass_count=3,
        in_flight=0,
        dispatched_gen=0,
        dispatched_ver=0,
    )
    assert code == IDLE_USER_BLOCKED
    assert "search branches exhausted automatic repair budget" in detail


def test_explicit_user_branch_hint_reopens_exhausted_search_branch() -> None:
    cand = _cand(
        repair_count=_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT + 1,
        repair_hint=(
            "[verifier]\nsame gap\n---\n"
            f"[user @ now]\n{_PHASE2_USER_BRANCH_OVERRIDE}\n"
            "Use the local-field vector-space topology lemma instead."
        ),
    )

    assert not _is_search_branch_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_budget_exhaustion_applies_to_proof_requiring_kinds_only() -> None:
    exhausted = _PHASE2_MAX_AUTOMATIC_REPAIR_COUNT + 1
    for kind in ("lemma", "theorem", "proposition"):
        assert _is_search_branch_stuck(_cand(target_kind=kind, repair_count=exhausted))
    for kind in ("definition", "external_theorem"):
        assert not _is_search_branch_stuck(_cand(target_kind=kind, repair_count=exhausted))


def test_exhausted_branch_is_not_reported_as_dependency_blocked() -> None:
    cand = _cand(
        repair_count=_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT + 1,
        dep_statement_hashes={"def:x": "ef" * 32},
        dep_pass_counts={"def:x": 0},
    )

    code, detail = _decide_idle_reason(
        _KBSnapshot(candidates=[cand]),
        desired_pass_count=3,
        in_flight=0,
        dispatched_gen=0,
        dispatched_ver=0,
    )
    assert code == IDLE_USER_BLOCKED
    assert "search branches exhausted automatic repair budget" in detail


def test_below_budget_missing_deps_remains_generation_dependency_blocked() -> None:
    cand = _cand(
        repair_count=_PHASE2_MAX_AUTOMATIC_REPAIR_COUNT,
        dep_statement_hashes={"def:x": "ef" * 32},
        dep_pass_counts={"def:x": 0},
    )

    code, detail = _decide_idle_reason(
        _KBSnapshot(candidates=[cand]),
        desired_pass_count=3,
        in_flight=0,
        dispatched_gen=0,
        dispatched_ver=0,
    )
    assert code == IDLE_GEN_DEP_BLOCKED
    assert "generator candidates blocked on deps" in detail


def test_generator_introduced_generic_background_helper_stops_after_one_rejection() -> None:
    cand = _cand(
        target="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
        target_kind="lemma",
        repair_count=1,
        statement=(
            "Let K be a field and X a smooth K-variety with a K-rational point. "
            "Then X admits an etale affine-space chart near that point."
        ),
        verification_report="uncited Jacobian criterion and etale local model",
        introduced_by_actor="generator:codex-default",
    )

    assert _is_generic_background_stuck(cand)
    [item] = _search_branch_stuck_targets([cand])
    assert item["kind"] == "generic_background_stuck"
    assert item["trigger"] == "generic_background_expansion"
    assert item["reason"] == "generator_background_helper_rejected"
    assert item["target"] == cand.target

    code, detail = _decide_idle_reason(
        _KBSnapshot(candidates=[cand]),
        desired_pass_count=3,
        in_flight=0,
        dispatched_gen=0,
        dispatched_ver=0,
    )
    assert code == IDLE_USER_BLOCKED
    assert "background expansion guard" in detail


def test_explicit_user_branch_hint_reopens_generic_background_helper() -> None:
    cand = _cand(
        target="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
        target_kind="lemma",
        repair_count=1,
        statement=(
            "Let K be a field and X a smooth K-variety with a K-rational point. "
            "Then X admits an etale affine-space chart near that point."
        ),
        verification_report="uncited Jacobian criterion and etale local model",
        repair_hint=(
            "[verifier]\nuncited etale chart\n---\n"
            f"[user @ now]\n{_PHASE2_USER_BRANCH_OVERRIDE}\n"
            "Do not repair this AG chart; replace it with a local-field "
            "vector-space topology branch."
        ),
        introduced_by_actor="generator:codex-default",
    )

    assert not _is_generic_background_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_user_hint_reopens_generic_background_helper_without_magic_token() -> None:
    cand = _cand(
        target="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
        target_kind="lemma",
        repair_count=1,
        statement=(
            "Let F be a Hausdorff non-discrete topological field and W a "
            "finite-dimensional F-vector space. Every nonempty Zariski open "
            "subset of W is open dense in the usual topology."
        ),
        verification_report="needs a proof that nonempty open subsets of F are infinite",
        repair_hint=(
            "[verifier]\nFill the density gap.\n---\n"
            "[user @ now]\nUse the local-field vector-space topology lemma; "
            "do not continue the etale-chart route."
        ),
        introduced_by_actor="generator:codex-default",
    )

    assert not _is_generic_background_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_generic_guard_does_not_fire_on_label_only() -> None:
    cand = _cand(
        target="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
        target_kind="lemma",
        repair_count=1,
        statement="This is now a problem-specific local topological vector-space lemma.",
        verification_report="one local density step is missing",
        repair_hint="Fill the density step.",
        introduced_by_actor="generator:codex-default",
    )

    assert not _is_generic_background_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_zariski_vector_space_topology_branch_keeps_normal_repair_budget() -> None:
    cand = _cand(
        target="lem:smooth_k_variety_rational_point_has_etale_affine_space_chart",
        target_kind="lemma",
        repair_count=1,
        statement=(
            "Let F be a Hausdorff non-discrete topological field and W a "
            "finite-dimensional F-vector space. Every nonempty Zariski-open "
            "subset of W is open and dense for the usual product topology."
        ),
        verification_report=(
            "The only remaining gap is proving every nonempty open subset "
            "of F is infinite."
        ),
        repair_hint=(
            "Provide a correct proof that every nonempty open subset of a "
            "Hausdorff non-discrete topological field is infinite."
        ),
        introduced_by_actor="generator:codex-default",
    )

    assert not _is_generic_background_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_phase2_reroutes_parent_of_stuck_background_helper() -> None:
    stuck = _cand(
        target="lem:smooth_k_rational_point_local_ring_is_formally_smooth_of_dimension_n",
        target_kind="lemma",
        repair_count=1,
        statement="Smooth variety local-ring formally smooth helper.",
        verification_report="uncited etale chart",
        introduced_by_actor="generator:codex-default",
    )
    parent = _cand(
        target="lem:regular_parameters_give_completed_power_series_coordinates",
        target_kind="lemma",
        pass_count=0,
        repair_count=0,
        dep_statement_hashes={stuck.target: "ef" * 32, "lem:accepted": "12" * 32},
        dep_pass_counts={stuck.target: -1, "lem:accepted": 3},
        last_rejected_verification_hash="cd" * 32,
    )

    stuck_labels = {item["target"] for item in _search_branch_stuck_targets([stuck])}
    assert stuck_labels == {stuck.target}
    assert _phase2_reroute_parent_labels([stuck, parent], stuck_labels) == {
        parent.target
    }

    by_label = {stuck.target: stuck, parent.target: parent}
    rerouted = _phase2_reroute_candidate(parent, by_label, stuck_labels)
    assert rerouted.pass_count == -1
    assert rerouted.repair_count == 1
    assert rerouted.last_rejected_verification_hash == parent.verification_hash
    assert stuck.target in rerouted.repair_hint
    assert "phase2:reroute_around_stuck_background" in rerouted.repair_hint


def test_phase2_reroute_skips_generic_background_ancestors() -> None:
    stuck = _cand(
        target="lem:finite_formally_etale_local_algebra_has_standard_etale_local_model",
        target_kind="lemma",
        repair_count=1,
        statement="Finite formally etale local algebra has a standard etale local model.",
        verification_report="Nakayama gap in local ring argument",
        introduced_by_actor="generator:codex-default",
    )
    background_parent = _cand(
        target="lem:jacobian_unit_local_presentation_is_etale_at_point",
        target_kind="lemma",
        pass_count=0,
        repair_count=0,
        statement="Jacobian unit local presentation is etale at the point.",
        dep_statement_hashes={stuck.target: "ef" * 32},
        dep_pass_counts={stuck.target: -1},
        introduced_by_actor="generator:codex-default",
    )
    background_bridge = _cand(
        target="prop:equal_dimension_local_field_orbit_closures_are_equal",
        target_kind="proposition",
        pass_count=0,
        repair_count=0,
        statement="Equal-dimension local-field orbit closures are equal.",
        dep_statement_hashes={background_parent.target: "12" * 32},
        dep_pass_counts={background_parent.target: 0},
        introduced_by_actor="generator:codex-default",
    )
    theorem_parent = _cand(
        target="thm:induced_orbit_toy_problem",
        target_kind="theorem",
        pass_count=0,
        repair_count=0,
        statement="The induced orbit toy problem.",
        dep_statement_hashes={background_bridge.target: "34" * 32},
        dep_pass_counts={background_bridge.target: 0},
        introduced_by_actor="user:cli",
    )

    stuck_labels = {item["target"] for item in _search_branch_stuck_targets([stuck])}
    assert _phase2_reroute_parent_labels(
        [stuck, background_parent, background_bridge, theorem_parent], stuck_labels
    ) == {theorem_parent.target}

    by_label = {
        stuck.target: stuck,
        background_parent.target: background_parent,
        background_bridge.target: background_bridge,
        theorem_parent.target: theorem_parent,
    }
    rerouted = _phase2_reroute_candidate(theorem_parent, by_label, stuck_labels)
    assert stuck.target in rerouted.repair_hint
    assert background_parent.target not in _phase2_reroute_parent_labels(
        [stuck, background_parent, background_bridge, theorem_parent], stuck_labels
    )
    assert background_bridge.target not in _phase2_reroute_parent_labels(
        [stuck, background_parent, background_bridge, theorem_parent], stuck_labels
    )


def test_phase2_reroute_skips_orbit_topology_bridge_proposition() -> None:
    stuck = _cand(
        target="lem:smooth_k_rational_point_local_ring_is_formally_smooth_of_dimension_n",
        target_kind="lemma",
        repair_count=1,
        statement="Smooth variety local ring is formally smooth.",
        verification_report="missing etale chart input",
        introduced_by_actor="generator:codex-default",
    )
    bridge = _cand(
        target="prop:equal_dimension_local_field_orbit_closures_are_equal",
        target_kind="proposition",
        pass_count=0,
        repair_count=0,
        statement="Equal-dimensional local-field orbit closures are equal.",
        dep_statement_hashes={stuck.target: "ef" * 32},
        dep_pass_counts={stuck.target: -1},
        introduced_by_actor="generator:codex-default",
    )
    theorem = _cand(
        target="thm:induced_orbit_toy_problem",
        target_kind="theorem",
        pass_count=0,
        repair_count=0,
        statement="Induced orbit toy theorem.",
        dep_statement_hashes={bridge.target: "12" * 32},
        dep_pass_counts={bridge.target: 0},
        introduced_by_actor="user:cli",
    )

    stuck_labels = {item["target"] for item in _search_branch_stuck_targets([stuck])}
    assert _phase2_reroute_parent_labels([stuck, bridge, theorem], stuck_labels) == {
        theorem.target
    }


def test_problem_specific_generator_helper_keeps_normal_repair_budget() -> None:
    cand = _cand(
        target="lem:block_form_for_x0_plus_u",
        target_kind="lemma",
        repair_count=1,
        statement="Every element of X0 plus the parabolic nilradical has the stated block form.",
        verification_report="local sign error in the adjoint relation",
        introduced_by_actor="generator:codex-default",
    )

    assert not _is_generic_background_stuck(cand)
    assert _search_branch_stuck_targets([cand]) == []


def test_generator_introduced_generic_background_definition_is_guarded() -> None:
    cand = _cand(
        target="def:standard_etale_morphism",
        target_kind="definition",
        proof="",
        repair_count=1,
        statement="A standard etale morphism is a local algebraic model.",
        verification_report="definition too broad for the induced orbit proof",
        introduced_by_actor="generator:codex-default",
    )

    assert _is_generic_background_stuck(cand)
    [item] = _search_branch_stuck_targets([cand])
    assert item["kind"] == "generic_background_stuck"
