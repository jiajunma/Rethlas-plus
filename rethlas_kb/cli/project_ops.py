"""``status`` + ``open-questions`` subcommands (issue #15).

These are read-only project-aware commands. They don't invoke any
backend — they only read the KB + project manifest and emit a
summary or work-list. Useful for dashboarding ("how much of the
project is admitted?") and prioritising ("what should I tackle
next?").
"""

from __future__ import annotations

import argparse
import json
import sys

from rethlas_kb.project import (
    ProjectError,
    list_projects,
    load_project,
    open_questions,
    project_status,
)

from ._constants import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from ._io import adapter_for


def add_subparsers(sub) -> None:
    _add_status(sub)
    _add_open_questions(sub)
    _add_list_projects(sub)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def _add_status(sub) -> None:
    p = sub.add_parser(
        "status",
        help="Project dashboard: counts by status / kind, done-ratio.",
        description=(
            "Read-only summary of a project's closure: how many nodes "
            "in scope, how many admitted vs staged vs missing, "
            "breakdown by status and kind."
        ),
    )
    p.add_argument(
        "--project", required=True,
        help="Project id (loads .rethlas-kb/projects/<id>.yml).",
    )
    p.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    p.set_defaults(handler=_cmd_status)


def _cmd_status(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        project = load_project(ns.project, adapter.kb_root)
    except ProjectError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_USAGE

    status = project_status(project, adapter)

    if ns.json:
        print(json.dumps({
            "project_id": status.project_id,
            "title": project.title,
            "project_status": project.status,
            "goal_count": status.goal_count,
            "closure_count": status.closure_count,
            "admitted_count": status.admitted_count,
            "staged_count": status.staged_count,
            "missing_count": status.missing_count,
            "done_ratio": round(status.done_ratio, 3),
            "by_status": dict(status.by_status),
            "by_kind": dict(status.by_kind),
        }, indent=2, ensure_ascii=False))
        return EXIT_OK

    # Human-readable dashboard
    print(f"# project: {status.project_id}")
    if project.title:
        print(f"  title:        {project.title}")
    print(f"  status:       {project.status}")
    print(f"  goal nodes:   {status.goal_count}")
    print(f"  closure:      {status.closure_count}")
    print(f"  admitted:     {status.admitted_count} "
          f"({status.done_ratio:.0%})")
    print(f"  staged:       {status.staged_count}")
    print(f"  missing:      {status.missing_count}")
    print()
    if status.by_status:
        print("  by status:")
        for k, v in status.by_status.items():
            print(f"    {k:<10} {v}")
    if status.by_kind:
        print("  by kind:")
        for k, v in status.by_kind.items():
            print(f"    {k:<20} {v}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# open-questions
# ---------------------------------------------------------------------------
def _add_open_questions(sub) -> None:
    p = sub.add_parser(
        "open-questions",
        help="List prioritised open work items for a project.",
        description=(
            "Closure minus admitted nodes, sorted by distance from the "
            "nearest goal (closer = higher priority). Missing nodes "
            "(referenced by uses: but absent from the KB) are flagged."
        ),
    )
    p.add_argument(
        "--project", required=True,
        help="Project id (loads .rethlas-kb/projects/<id>.yml).",
    )
    p.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    p.add_argument(
        "--limit", type=int, default=0,
        help="Cap the output to the top N items (default: no cap).",
    )
    p.set_defaults(handler=_cmd_open_questions)


def _cmd_open_questions(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        project = load_project(ns.project, adapter.kb_root)
    except ProjectError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_USAGE

    items = open_questions(project, adapter)
    if ns.limit and ns.limit > 0:
        items = items[: ns.limit]

    if ns.json:
        print(json.dumps([
            {
                "node_id": q.node_id,
                "distance": q.distance,
                "status": q.status,
                "kind": q.kind,
                "title": q.title,
                "is_missing": q.is_missing,
            }
            for q in items
        ], indent=2, ensure_ascii=False))
        return EXIT_OK

    if not items:
        print(f"# project {project.id!r}: no open questions — everything "
              "in the closure is admitted.")
        return EXIT_OK

    print(f"# project {project.id!r} — {len(items)} open question(s)")
    print(f"# columns: dist | status | kind | id | title")
    for q in items:
        flag = "MISSING" if q.is_missing else q.status
        kind = q.kind or "—"
        print(f"  {q.distance:<4} {flag:<12} {kind:<20} {q.node_id:<50} {q.title}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# list-projects (small helper for users to see what's defined)
# ---------------------------------------------------------------------------
def _add_list_projects(sub) -> None:
    p = sub.add_parser(
        "list-projects",
        help="List project ids defined under .rethlas-kb/projects/.",
    )
    p.add_argument("--blueprint", default=".", help="Blueprint root.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_cmd_list_projects)


def _cmd_list_projects(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    ids = list_projects(adapter.kb_root)
    if ns.json:
        print(json.dumps(ids, indent=2, ensure_ascii=False))
        return EXIT_OK
    if not ids:
        print(
            "# no projects defined yet. Create one with:\n"
            "#   mkdir -p .rethlas-kb/projects\n"
            "#   $EDITOR .rethlas-kb/projects/main.yml",
            file=sys.stderr,
        )
        return EXIT_OK
    for pid in ids:
        print(pid)
    return EXIT_OK
