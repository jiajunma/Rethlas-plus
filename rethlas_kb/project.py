"""Research-project abstraction (issue #14).

A *project* is a goal-driven slice of an mdblueprint KB: the user
declares a few *goal nodes* (the main theorems / results they care
about), and rethlas-kb computes the **closure** (everything those
goals transitively depend on via ``uses:``) plus **open questions**
(closure members that aren't yet admitted, ranked by how close they
are to a goal).

Projects live as YAML files inside the **blueprint repo**, not
inside rethlas-kb:

    <blueprint>/.rethlas-kb/projects/<id>.yml

so they're version-controlled with the math itself. Schema:

```yaml
id: main
title: Sheaves on Buildings — main theorem
goal_nodes:
  - applications.classical_main_theorem
  - applications.tame_main_theorem
created: 2026-05-25
status: active        # active | paused | done
notes: "Tracking the main result of the paper"
```

Only ``id`` and ``goal_nodes`` are required; the rest are optional
metadata. The loader validates required fields and rejects unknown
status values.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover

    from rethlas_kb.adapter import KbAdapter


VALID_PROJECT_STATUSES = frozenset({"active", "paused", "done"})


class ProjectError(Exception):
    """Raised on a malformed project manifest or missing manifest file."""


@dataclass(frozen=True)
class Project:
    """One research project — typed view of a manifest YAML."""

    id: str
    title: str
    goal_nodes: tuple[str, ...]
    status: str = "active"
    notes: str = ""
    created: str = ""  # ISO date string; kept as str for round-trip simplicity
    file_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ProjectError("project id must be non-empty")
        if not self.goal_nodes:
            raise ProjectError(
                f"project {self.id!r} has no goal_nodes — "
                f"a project must declare at least one goal"
            )
        if self.status not in VALID_PROJECT_STATUSES:
            raise ProjectError(
                f"project {self.id!r} has invalid status {self.status!r}; "
                f"expected one of {sorted(VALID_PROJECT_STATUSES)}"
            )


@dataclass(frozen=True)
class OpenQuestion:
    """One node in the closure that isn't admitted yet."""

    node_id: str
    distance: int     # graph distance from the *nearest* goal (0 = is a goal)
    status: str       # mdblueprint status string ("staged" / missing / etc.)
    title: str = ""
    kind: str = ""
    # When True, the node is referenced by a closure member but not actually
    # in the KB (the ``uses:`` points at something that doesn't exist).
    is_missing: bool = False


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def project_path(blueprint_root: Path | str, project_id: str) -> Path:
    """Return the canonical YAML path for a project under the given blueprint."""
    return (
        Path(blueprint_root) / ".rethlas-kb" / "projects" / f"{project_id}.yml"
    )


def load_project(project_id: str, blueprint_root: Path | str) -> Project:
    """Read ``.rethlas-kb/projects/<id>.yml`` from the blueprint root."""
    path = project_path(blueprint_root, project_id)
    if not path.exists():
        raise ProjectError(
            f"project manifest not found: {path} "
            f"(create it with `mkdir -p .rethlas-kb/projects && "
            f"$EDITOR .rethlas-kb/projects/{project_id}.yml`)"
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProjectError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectError(
            f"manifest {path} must contain a YAML mapping at top level"
        )

    fid = raw.get("id") or project_id
    if fid != project_id:
        raise ProjectError(
            f"manifest {path} declares id={fid!r} but filename is "
            f"{project_id!r} — they must match"
        )

    goal_nodes_raw = raw.get("goal_nodes") or []
    if not isinstance(goal_nodes_raw, list):
        raise ProjectError(
            f"manifest {path}: goal_nodes must be a list"
        )
    if not all(isinstance(g, str) and g.strip() for g in goal_nodes_raw):
        raise ProjectError(
            f"manifest {path}: every goal_node must be a non-empty string"
        )

    created = raw.get("created")
    if isinstance(created, date):
        created_str = created.isoformat()
    elif created is None:
        created_str = ""
    elif isinstance(created, str):
        created_str = created
    else:
        raise ProjectError(
            f"manifest {path}: created must be an ISO date string"
        )

    notes_raw = raw.get("notes", "")
    if notes_raw is None:
        notes_raw = ""
    if not isinstance(notes_raw, str):
        raise ProjectError(f"manifest {path}: notes must be a string")

    return Project(
        id=fid,
        title=str(raw.get("title", "")),
        goal_nodes=tuple(goal_nodes_raw),
        status=str(raw.get("status", "active")),
        notes=notes_raw,
        created=created_str,
        file_path=path,
    )


def list_projects(blueprint_root: Path | str) -> list[str]:
    """Return all project ids under the blueprint root (sorted)."""
    base = Path(blueprint_root) / ".rethlas-kb" / "projects"
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.yml"))


# ---------------------------------------------------------------------------
# Closure + open questions
# ---------------------------------------------------------------------------
def closure(project: Project, adapter: "KbAdapter") -> set[str]:
    """Return the set of node ids transitively reachable from goal_nodes via ``uses:``.

    Includes the goal nodes themselves. Missing references (nodes named
    in a ``uses:`` list but absent from the KB) are included as ids —
    the caller can detect them via :func:`open_questions` which marks
    them ``is_missing=True``.
    """
    found: set[str] = set()
    queue: deque[str] = deque(project.goal_nodes)
    while queue:
        nid = queue.popleft()
        if nid in found:
            continue
        found.add(nid)
        try:
            node = adapter.read_node(nid)
        except KeyError:
            # Missing dep — record the id but don't expand further.
            continue
        for dep in node.uses or []:
            if dep not in found:
                queue.append(dep)
    return found


def closure_distances(
    project: Project, adapter: "KbAdapter",
) -> dict[str, int]:
    """Like :func:`closure`, but also returns distance from nearest goal.

    Distance 0 = is itself a goal. Distance N = N edges away in the
    ``uses`` DAG. When a node is reachable from multiple goals, the
    smaller distance wins.
    """
    dist: dict[str, int] = {g: 0 for g in project.goal_nodes}
    # Multi-source BFS so the first time we reach a node is the minimum.
    queue: deque[tuple[str, int]] = deque((g, 0) for g in project.goal_nodes)
    visited: set[str] = set(project.goal_nodes)
    while queue:
        nid, d = queue.popleft()
        try:
            node = adapter.read_node(nid)
        except KeyError:
            continue
        for dep in node.uses or []:
            if dep in visited:
                continue
            visited.add(dep)
            dist[dep] = d + 1
            queue.append((dep, d + 1))
    return dist


def open_questions(
    project: Project, adapter: "KbAdapter",
) -> list[OpenQuestion]:
    """Return closure members that aren't yet admitted, ordered priority-first.

    Ordering:
      1. By distance from nearest goal (closer = higher priority — those
         block the goal more directly).
      2. Then by node id (alphabetical) for stable output.

    Missing nodes (referenced but absent from KB) appear with
    ``is_missing=True``. Admitted nodes are excluded.
    """
    from tools.knowledge.models import ADMITTED_STATUSES

    dist = closure_distances(project, adapter)
    items: list[OpenQuestion] = []
    for nid, d in dist.items():
        try:
            node = adapter.read_node(nid)
        except KeyError:
            items.append(OpenQuestion(
                node_id=nid, distance=d, status="missing",
                title="", kind="", is_missing=True,
            ))
            continue
        if node.status in ADMITTED_STATUSES:
            continue
        items.append(OpenQuestion(
            node_id=nid, distance=d,
            status=node.status, title=node.title, kind=node.kind,
            is_missing=False,
        ))
    items.sort(key=lambda q: (q.distance, q.node_id))
    return items


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProjectStatus:
    """Counts summary for ``rethlas-kb status --project <id>``."""

    project_id: str
    goal_count: int
    closure_count: int
    admitted_count: int
    staged_count: int
    missing_count: int
    by_status: dict[str, int] = field(default_factory=dict)
    by_kind: dict[str, int] = field(default_factory=dict)

    @property
    def done_ratio(self) -> float:
        """Fraction of the closure that is already admitted, in [0, 1]."""
        if self.closure_count == 0:
            return 0.0
        return self.admitted_count / self.closure_count


def project_status(project: Project, adapter: "KbAdapter") -> ProjectStatus:
    """Compute the closure summary for the dashboard / status command."""
    from tools.knowledge.models import ADMITTED_STATUSES, STAGED_STATUSES

    ids = closure(project, adapter)
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    admitted = 0
    staged = 0
    missing = 0
    for nid in ids:
        try:
            node = adapter.read_node(nid)
        except KeyError:
            missing += 1
            by_status["missing"] = by_status.get("missing", 0) + 1
            continue
        by_status[node.status] = by_status.get(node.status, 0) + 1
        by_kind[node.kind] = by_kind.get(node.kind, 0) + 1
        if node.status in ADMITTED_STATUSES:
            admitted += 1
        elif node.status in STAGED_STATUSES:
            staged += 1
    return ProjectStatus(
        project_id=project.id,
        goal_count=len(project.goal_nodes),
        closure_count=len(ids),
        admitted_count=admitted,
        staged_count=staged,
        missing_count=missing,
        by_status=dict(sorted(by_status.items())),
        by_kind=dict(sorted(by_kind.items())),
    )


__all__ = [
    "OpenQuestion",
    "Project",
    "ProjectError",
    "ProjectStatus",
    "VALID_PROJECT_STATUSES",
    "closure",
    "closure_distances",
    "list_projects",
    "load_project",
    "open_questions",
    "project_path",
    "project_status",
]
