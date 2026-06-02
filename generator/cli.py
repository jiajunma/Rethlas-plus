"""``rethlas generator`` — standalone generator dispatch (PHASE1 M6 system test).

The CLI:

1. Reads the workspace KB (Kuzu, read-only) for the target's
   ``statement`` / ``proof`` / ``dep_statement_hashes`` /
   ``verification_hash`` / ``repair_hint`` / ``repair_count``.
2. Writes a ``runtime/jobs/{job_id}.json`` with the fresh/repair
   context.
3. Invokes :func:`generator.role.main` *in-process* (the M8 path
   spawns it as a subprocess via :func:`common.runtime.spawn`).

The CLI is the user-facing equivalent of "what coordinator does"; both
paths produce the same job file shape so wrappers do not branch.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cli.workspace import ensure_initialised, workspace_paths
from common.runtime.jobs import (
    JobRecord,
    STATUS_STARTING,
    job_file_path,
    log_path_for,
    make_job_id,
    utc_now_iso,
    write_job_file,
)


# argparse choices for --mode so an invalid value exits 2 (PHASE1 system test).
_MODE_CHOICES = ("fresh", "repair")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rethlas generator",
        description="Run a generator attempt against the local workspace.",
    )
    p.add_argument("--target", required=True, help="target node label")
    p.add_argument("--mode", required=True, choices=_MODE_CHOICES)
    p.add_argument(
        "--codex-argv",
        default="",
        help="Override Codex argv (space-separated); tests pass fake_codex here.",
    )
    p.add_argument(
        "--silent-timeout-s",
        type=float,
        default=1800.0,
        help="codex_silent_timeout_seconds (§7.4)",
    )
    p.add_argument(
        "--actor",
        default="generator:cli",
        help="actor name for emitted events",
    )
    p.add_argument(
        "--workspace",
        default=None,
        help="workspace path (default: cwd)",
    )
    return p


def _read_target_context(ws_root: Path, target: str) -> tuple[dict, str | None, str | None]:
    """Open the workspace KB read-only and return (target_fields, h_rejected_or_none, error_or_none).

    On error returns ``({}, None, "<message>")``.
    """
    # §13: math from the markdown KB (no Kuzu). The operational fields
    # repair_hint / repair_count / verification_report live in the librarian's
    # in-memory projection, NOT markdown (decision 乙); the coordinator injects
    # them into the dispatched job (§13.4). This standalone CLI path defaults
    # them empty — fresh-mode generation needs only the math; repair-mode hints
    # arrive via the coordinator, not by reading the KB directly.
    from common.kb.markdown_reader import read_nodes_dir

    nodes_dir = ws_root / "knowledge_base" / "nodes"
    if not nodes_dir.is_dir():
        return {}, None, "knowledge_base/nodes does not exist (run rethlas supervise first)"
    nodes = read_nodes_dir(nodes_dir)
    node = nodes.get(target)
    if node is None:
        return {}, None, f"label {target!r} not found in KB"
    deps = {
        dep: (nodes[dep].statement_hash if dep in nodes else "")
        for dep in node.depends_on
    }
    target_fields = {
        "target_kind": node.kind.value,
        "statement": node.statement,
        "proof": node.proof or "",
        "statement_hash": node.statement_hash,
        "verification_hash": node.verification_hash,
        "repair_hint": "",
        "repair_count": 0,
        "verification_report": "",
        "dep_statement_hashes": deps,
    }
    return target_fields, target_fields["verification_hash"], None


def run_generator(workspace: str | None, args: argparse.Namespace) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)

    fields, h_current, err = _read_target_context(ws.root, args.target)
    if err is not None:
        sys.stderr.write(f"generator: {err}\n")
        return 2

    # Build job file.
    iso_ms_dt = utc_now_iso()
    iso_ms = iso_ms_dt.replace("-", "").replace(":", "").replace(".", ".").replace("Z", "")
    # iso_ms expected shape YYYYMMDDTHHMMSS.mmm — derive from utc_now.
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc)
    iso_ms = now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}"

    import secrets
    uid = secrets.token_hex(8)
    job_id = make_job_id("generator", iso_ms=iso_ms, uid=uid)
    log_rel = f"runtime/logs/{job_id}.codex.log"

    # H_rejected: in repair mode, the *current* verification_hash is the one
    # the last verdict rejected (verifier carries it). For fresh mode it is empty.
    h_rejected = h_current if args.mode == "repair" else ""

    rec = JobRecord(
        job_id=job_id,
        kind="generator",
        target=args.target,
        mode=args.mode,
        dispatch_hash=fields["verification_hash"],
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=iso_ms_dt,
        updated_at=iso_ms_dt,
        status=STATUS_STARTING,
        log_path=log_rel,
        target_kind=fields["target_kind"],
        statement=fields["statement"],
        proof=fields["proof"],
        dep_statement_hashes=fields["dep_statement_hashes"],
        verification_report=fields["verification_report"],
        repair_hint=fields["repair_hint"],
        repair_count=fields["repair_count"],
        h_rejected=h_rejected,
    )
    write_job_file(job_file_path(ws.runtime_jobs, job_id), rec)

    # Invoke role.py inline.
    os.environ["RETHLAS_WORKSPACE"] = str(ws.root)
    from generator.role import main as role_main
    role_args = [job_id]
    if args.codex_argv:
        role_args.extend(["--codex-argv", args.codex_argv])
    role_args.extend(["--silent-timeout-s", str(args.silent_timeout_s)])
    role_args.extend(["--actor", args.actor])
    return role_main(role_args)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_generator(args.workspace, args)


__all__ = ["main", "run_generator"]
