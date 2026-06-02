from __future__ import annotations

import json
import shutil
from pathlib import Path

from cli.workspace import workspace_paths
from linter.main import run_linter_on_workspace
from tests.fixtures.tmp_workspace import make_workspace


ROOT = Path(__file__).resolve().parents[2]
MANUAL_RUN = ROOT / "tests" / "manual_runs" / "phase3_moeglin_waldspurger_1987_20260504"
INITIAL_BATCH_IDS = {
    "20260504T054552.177-0001-0b6b3aaf5ca36d7f": 6,
    "20260504T063645.797-0001-62c81532048d8dfc": 11,
    "20260504T064715.061-0001-ac1c2dc49c1eb9cc": 2,
    "20260504T065136.081-0001-c5be066c0024dcf4": 1,
    "20260504T080624.794-0001-befc0efe3d96975c": 2,
}
EXPECTED_LABELS = {
    "def:degenerate_whittaker_setup",
    "def:degenerate_whittaker_forms",
    "def:whittaker_orbit_sets",
    "ext:harish_chandra_local_character_expansion",
    "ext:rodier_whittaker_model_criterion",
    "ext:moeglin_waldspurger_main_theorem",
    "lem:mw_i_3_compact_subgroup_bch",
    "lem:mw_i_6_character_stabilizer",
    "lem:mw_i_10_nonzero_invariants",
    "prop:mw_i_11_orbit_from_whittaker",
    "lem:mw_i_12_dimension_coefficient",
    "lem:mw_i_13_trivial_action",
    "prop:mw_i_14_jacquet_injection",
    "lem:mw_i_15_transition_injective",
    "prop:mw_ii_1_3_regular_principal_series_orbits",
    "prop:mw_ii_2_gl_n_unique_nilpotent_orbit",
    "lem:mw_ii_3_2_concentration_criterion",
    "prop:mw_ii_3_1_small_rank_orbits",
    "lem:mw_ii_3_3_rank_projection",
    "prop:mw_ii_1_3_whittaker_dimension_formula",
}


def test_saved_moeglin_waldspurger_learner_run_is_reusable(tmp_path: Path) -> None:
    """Guard the expensive real learner run without spending model tokens."""
    make_workspace(tmp_path, seed_config=True)

    _copy_tree(MANUAL_RUN / "events", tmp_path / "events")
    _copy_tree(MANUAL_RUN / "knowledge_base" / "phase3", tmp_path / "knowledge_base" / "phase3")
    _copy_tree(MANUAL_RUN / "sources", tmp_path / "sources")

    rc = run_linter_on_workspace(workspace_paths(str(tmp_path)))
    assert rc == 0

    batch_dir = tmp_path / "knowledge_base" / "phase3" / "learner_batches"
    artifacts = sorted(batch_dir.glob("*.json"))
    assert set(INITIAL_BATCH_IDS) <= {path.stem for path in artifacts}

    labels: set[str] = set()
    initial_candidates = 0
    initial_verification_requests = 0
    initial_issues = 0

    for artifact in artifacts:
        data = json.loads(artifact.read_text(encoding="utf-8"))
        payload = data["payload"]

        if artifact.stem in INITIAL_BATCH_IDS:
            assert data["source_id"] == "src:moeglin_waldspurger_whittaker_1987"
            assert len(payload["candidate_nodes"]) == INITIAL_BATCH_IDS[artifact.stem]

            initial_candidates += len(payload["candidate_nodes"])
            initial_verification_requests += len(payload["verification_requests"])
            initial_issues += len(payload["issues"])
        labels.update(node["label"] for node in payload["candidate_nodes"])

    assert initial_candidates == 22
    assert initial_verification_requests == 6
    assert initial_issues == 19
    assert EXPECTED_LABELS <= labels

    enriched = json.loads(
        (
            batch_dir
            / "20260504T080624.794-0001-befc0efe3d96975c.json"
        ).read_text(encoding="utf-8")
    )["payload"]
    by_label = {n["label"]: n for n in enriched["candidate_nodes"]}
    assert (
        by_label["prop:mw_i_11_orbit_from_whittaker"]["proof_status"]
        == "proof_sketch_extracted"
    )
    assert len(by_label["prop:mw_i_11_orbit_from_whittaker"]["proof_steps"]) == 4
    assert (
        by_label["lem:mw_i_12_dimension_coefficient"]["proof_status"]
        == "proof_incomplete"
    )
    assert len(by_label["lem:mw_i_12_dimension_coefficient"]["proof_steps"]) == 5
    assert len(enriched["bridge_requests"]) == 1

    from dashboard.server import DashboardCore

    kb = DashboardCore(tmp_path).study_kb()
    entries = {entry["label"]: entry for entry in kb["entries"]}
    hch = entries.get("ext:harish_chandra_local_character_expansion") or entries[
        "thm:hc_semisimple_local_character_expansion"
    ]
    assert hch["citation_status"] == "source_provisional"
    assert hch["bibliography"]["venue"] == "Math. Z."
    assert hch["bibliography"]["volume"] == "196"
    assert hch["bibliography"]["year"] == "1987"
    assert hch["bibliography"]["pages"] == "427-452"
    hch_refs = {
        ref["span_id"]: ref for ref in hch["external_source_refs"]
    }
    assert "span:mw_whittaker:rodier_character_expansion" in hch_refs
    assert hch_refs["span:mw_whittaker:rodier_character_expansion"]["page_range"] == "427-430"


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
