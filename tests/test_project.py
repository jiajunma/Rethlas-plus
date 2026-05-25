"""Project-manifest tests (issue #14)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.project import (
    OpenQuestion,
    Project,
    ProjectError,
    closure,
    closure_distances,
    list_projects,
    load_project,
    open_questions,
    project_path,
    project_status,
)


# ---------------------------------------------------------------------------
# Fixtures: small 5-node KB modeling a real dependency graph
# ---------------------------------------------------------------------------
GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.group
    title: Group
    kind: definition
    status: admitted
    primary_topic: algebra
    topics: [algebra]
    ---

    # Group
    """)

NORMAL_SUBGROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.normal_subgroup
    title: Normal Subgroup
    kind: definition
    status: admitted
    uses:
      - algebra.group
    primary_topic: algebra
    topics: [algebra]
    ---

    # Normal Subgroup
    """)

QUOTIENT_GROUP_MD = textwrap.dedent("""\
    ---
    id: algebra.quotient_group
    title: Quotient Group
    kind: definition
    status: staged
    uses:
      - algebra.group
      - algebra.normal_subgroup
    primary_topic: algebra
    topics: [algebra]
    ---

    # Quotient Group
    """)

ISO_THM_MD = textwrap.dedent("""\
    ---
    id: algebra.first_iso_theorem
    title: First Isomorphism Theorem
    kind: theorem
    status: staged
    uses:
      - algebra.quotient_group
      - algebra.group_homomorphism
    primary_topic: algebra
    topics: [algebra]
    ---

    # First Iso Theorem
    """)

# Note: algebra.group_homomorphism is referenced but NOT in the KB
# (missing dep — tests the closure's missing-handling).


@pytest.fixture
def kb(tmp_path: Path) -> KbAdapter:
    """Build a KB with one chain ending at first_iso_theorem.

    Dependency shape:

        first_iso_theorem (staged, goal)
          uses:
            quotient_group (staged)
              uses:
                group (admitted)
                normal_subgroup (admitted)
            group_homomorphism (MISSING — referenced but not in KB)
    """
    knowledge = tmp_path / "docs" / "knowledge"
    (knowledge / "nodes" / "algebra").mkdir(parents=True)
    (knowledge / "staged" / "algebra").mkdir(parents=True)
    (knowledge / "nodes" / "algebra" / "group.md").write_text(GROUP_MD)
    (knowledge / "nodes" / "algebra" / "normal_subgroup.md").write_text(
        NORMAL_SUBGROUP_MD
    )
    (knowledge / "staged" / "algebra" / "quotient_group.md").write_text(
        QUOTIENT_GROUP_MD
    )
    (knowledge / "staged" / "algebra" / "first_iso_theorem.md").write_text(
        ISO_THM_MD
    )
    return KbAdapter(tmp_path)


def _write_manifest(
    blueprint_root: Path, project_id: str, payload: dict,
) -> Path:
    base = blueprint_root / ".rethlas-kb" / "projects"
    base.mkdir(parents=True, exist_ok=True)
    p = base / f"{project_id}.yml"
    p.write_text(yaml.safe_dump(payload, sort_keys=False))
    return p


# ---------------------------------------------------------------------------
# Project dataclass invariants
# ---------------------------------------------------------------------------
def test_project_requires_nonempty_id() -> None:
    with pytest.raises(ProjectError, match="id must be non-empty"):
        Project(id="", title="x", goal_nodes=("a",))


def test_project_requires_at_least_one_goal_node() -> None:
    with pytest.raises(ProjectError, match="no goal_nodes"):
        Project(id="x", title="x", goal_nodes=())


def test_project_rejects_unknown_status() -> None:
    with pytest.raises(ProjectError, match="invalid status"):
        Project(id="x", title="x", goal_nodes=("a",), status="archived")


def test_project_accepts_valid_statuses() -> None:
    for status in ("active", "paused", "done"):
        p = Project(id="x", title="x", goal_nodes=("a",), status=status)
        assert p.status == status


# ---------------------------------------------------------------------------
# load_project — happy + error paths
# ---------------------------------------------------------------------------
def test_load_project_minimal(kb: KbAdapter) -> None:
    _write_manifest(kb.kb_root, "main", {
        "id": "main",
        "goal_nodes": ["algebra.first_iso_theorem"],
    })
    p = load_project("main", kb.kb_root)
    assert p.id == "main"
    assert p.goal_nodes == ("algebra.first_iso_theorem",)
    assert p.status == "active"  # default


def test_load_project_full_metadata(kb: KbAdapter) -> None:
    _write_manifest(kb.kb_root, "main", {
        "id": "main",
        "title": "First Isomorphism Theorem project",
        "goal_nodes": ["algebra.first_iso_theorem"],
        "status": "active",
        "notes": "Tracking the iso theorem prereqs.",
        "created": "2026-05-25",
    })
    p = load_project("main", kb.kb_root)
    assert p.title == "First Isomorphism Theorem project"
    assert p.notes.startswith("Tracking")
    assert p.created == "2026-05-25"
    assert p.file_path is not None
    assert p.file_path.name == "main.yml"


def test_load_project_missing_file_raises(kb: KbAdapter) -> None:
    with pytest.raises(ProjectError, match="project manifest not found"):
        load_project("does-not-exist", kb.kb_root)


def test_load_project_id_must_match_filename(kb: KbAdapter) -> None:
    _write_manifest(kb.kb_root, "main", {
        "id": "MISMATCHED",
        "goal_nodes": ["algebra.first_iso_theorem"],
    })
    with pytest.raises(ProjectError, match="declares id="):
        load_project("main", kb.kb_root)


def test_load_project_rejects_non_mapping_yaml(kb: KbAdapter) -> None:
    base = kb.kb_root / ".rethlas-kb" / "projects"
    base.mkdir(parents=True)
    (base / "main.yml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ProjectError, match="YAML mapping at top level"):
        load_project("main", kb.kb_root)


def test_load_project_rejects_goal_nodes_not_list(kb: KbAdapter) -> None:
    _write_manifest(kb.kb_root, "main", {
        "id": "main", "goal_nodes": "not-a-list",
    })
    with pytest.raises(ProjectError, match="goal_nodes must be a list"):
        load_project("main", kb.kb_root)


def test_load_project_rejects_empty_string_goal_node(kb: KbAdapter) -> None:
    _write_manifest(kb.kb_root, "main", {
        "id": "main", "goal_nodes": ["", "algebra.x"],
    })
    with pytest.raises(ProjectError, match="non-empty string"):
        load_project("main", kb.kb_root)


def test_load_project_accepts_date_yaml_value(kb: KbAdapter) -> None:
    """YAML auto-converts 2026-05-25 to a date object."""
    _write_manifest(kb.kb_root, "main", {
        "id": "main",
        "goal_nodes": ["algebra.first_iso_theorem"],
        # PyYAML returns datetime.date for 2026-05-25
    })
    # Write with a real date object in yaml
    base = kb.kb_root / ".rethlas-kb" / "projects"
    (base / "main.yml").write_text(
        "id: main\n"
        "goal_nodes: [algebra.first_iso_theorem]\n"
        "created: 2026-05-25\n"
    )
    p = load_project("main", kb.kb_root)
    assert p.created == "2026-05-25"


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------
def test_list_projects_empty_when_no_dir(kb: KbAdapter) -> None:
    assert list_projects(kb.kb_root) == []


def test_list_projects_returns_sorted_ids(kb: KbAdapter) -> None:
    _write_manifest(kb.kb_root, "zeta", {"id": "zeta", "goal_nodes": ["x"]})
    _write_manifest(kb.kb_root, "alpha", {"id": "alpha", "goal_nodes": ["x"]})
    _write_manifest(kb.kb_root, "mu", {"id": "mu", "goal_nodes": ["x"]})
    assert list_projects(kb.kb_root) == ["alpha", "mu", "zeta"]


# ---------------------------------------------------------------------------
# closure
# ---------------------------------------------------------------------------
def test_closure_includes_goal_itself(kb: KbAdapter) -> None:
    p = Project(id="p", title="p", goal_nodes=("algebra.group",))
    assert closure(p, kb) == {"algebra.group"}


def test_closure_walks_uses_transitively(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    found = closure(p, kb)
    assert "algebra.first_iso_theorem" in found
    assert "algebra.quotient_group" in found
    assert "algebra.group" in found  # 2 hops away
    assert "algebra.normal_subgroup" in found
    # Missing dep is still listed (caller can decide what to do)
    assert "algebra.group_homomorphism" in found


def test_closure_dedupes_across_multiple_goals(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.quotient_group", "algebra.normal_subgroup"),
    )
    # Both goals depend on algebra.group; it should appear once
    found = closure(p, kb)
    assert found == {
        "algebra.quotient_group", "algebra.normal_subgroup", "algebra.group",
    }


# ---------------------------------------------------------------------------
# closure_distances
# ---------------------------------------------------------------------------
def test_closure_distances_assigns_zero_to_goals(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    d = closure_distances(p, kb)
    assert d["algebra.first_iso_theorem"] == 0
    assert d["algebra.quotient_group"] == 1
    assert d["algebra.normal_subgroup"] == 2
    assert d["algebra.group"] == 2  # iso → quotient → group


def test_closure_distances_uses_minimum_when_multi_path(kb: KbAdapter) -> None:
    """algebra.group is reachable directly (as a goal) and via quotient (dist 1).
    The 0 must win.
    """
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.group", "algebra.quotient_group"),
    )
    d = closure_distances(p, kb)
    assert d["algebra.group"] == 0
    assert d["algebra.quotient_group"] == 0


# ---------------------------------------------------------------------------
# open_questions
# ---------------------------------------------------------------------------
def test_open_questions_excludes_admitted(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    qs = open_questions(p, kb)
    ids = [q.node_id for q in qs]
    # algebra.group and algebra.normal_subgroup are admitted → not in opens
    assert "algebra.group" not in ids
    assert "algebra.normal_subgroup" not in ids
    # staged + missing are in opens
    assert "algebra.first_iso_theorem" in ids
    assert "algebra.quotient_group" in ids
    assert "algebra.group_homomorphism" in ids


def test_open_questions_ordered_by_distance_then_id(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    qs = open_questions(p, kb)
    # Distance order: goal (0) < quotient (1) < hom (1, but missing)
    # Within distance 1, alphabetical: group_homomorphism < quotient_group
    assert [q.node_id for q in qs] == [
        "algebra.first_iso_theorem",       # d=0
        "algebra.group_homomorphism",      # d=1, missing
        "algebra.quotient_group",          # d=1, staged
    ]
    distances = [q.distance for q in qs]
    assert distances == sorted(distances)


def test_open_questions_flags_missing_nodes(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    qs = open_questions(p, kb)
    missing = [q for q in qs if q.is_missing]
    assert len(missing) == 1
    assert missing[0].node_id == "algebra.group_homomorphism"
    assert missing[0].status == "missing"
    assert missing[0].title == ""


def test_open_questions_carries_node_metadata(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    qs = open_questions(p, kb)
    iso = next(q for q in qs if q.node_id == "algebra.first_iso_theorem")
    assert iso.title == "First Isomorphism Theorem"
    assert iso.kind == "theorem"
    assert iso.status == "staged"
    assert iso.is_missing is False


# ---------------------------------------------------------------------------
# project_status
# ---------------------------------------------------------------------------
def test_project_status_counts(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    status = project_status(p, kb)
    assert status.project_id == "p"
    assert status.goal_count == 1
    assert status.closure_count == 5  # iso + quotient + group + normal + missing
    assert status.admitted_count == 2  # group + normal_subgroup
    assert status.staged_count == 2    # iso + quotient
    assert status.missing_count == 1   # group_homomorphism


def test_project_status_breakdown_by_status(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    status = project_status(p, kb)
    assert status.by_status.get("admitted") == 2
    assert status.by_status.get("staged") == 2
    assert status.by_status.get("missing") == 1


def test_project_status_breakdown_by_kind(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    status = project_status(p, kb)
    assert status.by_kind.get("definition") == 3  # group/normal/quotient
    assert status.by_kind.get("theorem") == 1     # iso_theorem
    # group_homomorphism is missing → no kind recorded


def test_project_status_done_ratio(kb: KbAdapter) -> None:
    p = Project(
        id="p", title="p",
        goal_nodes=("algebra.first_iso_theorem",),
    )
    status = project_status(p, kb)
    # 2 admitted / 5 closure
    assert status.done_ratio == pytest.approx(2 / 5)


def test_project_status_done_ratio_when_empty() -> None:
    status_obj = type(project_status)  # avoid running project_status
    # Direct construction with zero
    from rethlas_kb.project import ProjectStatus
    s = ProjectStatus(
        project_id="empty", goal_count=0, closure_count=0,
        admitted_count=0, staged_count=0, missing_count=0,
    )
    assert s.done_ratio == 0.0


# ---------------------------------------------------------------------------
# project_path
# ---------------------------------------------------------------------------
def test_project_path_canonical_layout(tmp_path: Path) -> None:
    p = project_path(tmp_path, "main")
    assert p == tmp_path / ".rethlas-kb" / "projects" / "main.yml"
