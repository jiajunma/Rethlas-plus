"""Linter orchestrator (ARCHITECTURE §6.6, PHASE1 M10).

``run_linter`` runs every category (A→F) without short-circuiting,
writes ``runtime/state/linter_report.json``, and returns the documented
exit code:

- ``0`` — no violations
- ``2`` — workspace not initialised, or supervise lock held without
  ``--allow-concurrent``
- ``5`` — at least one violation found

Linter is read-only by default. ``repair_nodes=True`` enables the
category-E ``--repair-nodes`` rewrite path; no other category mutates
state.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli.workspace import WorkspacePaths, ensure_initialised, workspace_paths
from linter.checks import (
    LinterReport,
    check_a_event_integrity,
    check_b_kb_structural,
    check_c_pass_count,
    check_d_repair_count,
    check_e_nodes_render,
    check_f_inventory,
)


LINTER_REPORT_SCHEMA = "rethlas-linter-report-v1"


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _supervise_lock_held(lock_path: Path) -> bool:
    """Return True if another process holds the workspace supervise lock."""
    if not lock_path.is_file():
        return False
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return True
            raise
        # We acquired it — release.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def write_report(path: Path, report: LinterReport, *, header_note: str = "") -> None:
    body: dict[str, Any] = {
        "schema": LINTER_REPORT_SCHEMA,
        "ts": _utc_now_iso(),
        **report.to_dict(),
    }
    if header_note:
        body["note"] = header_note
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_linter_on_workspace(
    ws: WorkspacePaths,
    *,
    repair_nodes: bool = False,
    allow_concurrent: bool = False,
) -> int:
    """Run every category and return the exit code."""
    ensure_initialised(ws)

    if _supervise_lock_held(ws.supervise_lock) and not allow_concurrent:
        sys.stderr.write(
            f"refusing to run with active supervise on {ws.root}; "
            "pass --allow-concurrent to override\n"
        )
        return 2

    header_note = ""
    if allow_concurrent and _supervise_lock_held(ws.supervise_lock):
        header_note = (
            "supervise lock held during linter run; transient drift entries "
            "may be benign"
        )

    db_path = ws.dag_kz
    a = check_a_event_integrity(ws.events)

    if db_path.exists():
        from common.kb.kuzu_backend import KuzuBackend
        backend = KuzuBackend(str(db_path))
        try:
            b = check_b_kb_structural(backend)
            c = check_c_pass_count(ws.events, backend)
            d = check_d_repair_count(ws.events, backend)
            e = check_e_nodes_render(backend, ws.nodes_dir, repair=repair_nodes)
            f = check_f_inventory(ws.events, backend)
        finally:
            backend.close()
    else:
        # No projection yet → categories B–F have nothing to audit.
        b = c = d = e = f = []

    report = LinterReport(a=a, b=b, c=c, d=d, e=e, f=f)
    write_report(
        ws.runtime_state / "linter_report.json", report, header_note=header_note
    )
    sys.stdout.write(report.to_dict()["summary"] + "\n")
    return 5 if report.total > 0 else 0


def run_linter(
    workspace: str | None,
    *,
    repair_nodes: bool = False,
    allow_concurrent: bool = False,
) -> int:
    return run_linter_on_workspace(
        workspace_paths(workspace),
        repair_nodes=repair_nodes,
        allow_concurrent=allow_concurrent,
    )


__all__ = ["LINTER_REPORT_SCHEMA", "run_linter", "run_linter_on_workspace", "write_report"]
