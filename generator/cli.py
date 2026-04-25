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
    import kuzu  # local import — CLI runs in the user process, Kuzu is fine here

    db_path = ws_root / "knowledge_base" / "dag.kz"
    if not db_path.is_dir() and not db_path.exists():
        return {}, None, "knowledge_base/dag.kz does not exist (run rethlas supervise first)"
    try:
        db = kuzu.Database(str(db_path), read_only=True)
        conn = kuzu.Connection(db)
    except Exception as exc:  # noqa: BLE001
        return {}, None, f"cannot open KB read-only: {exc}"
    try:
        res = conn.execute(
            "MATCH (n:Node {label: $lbl}) RETURN n.kind, n.statement, n.proof, "
            "n.statement_hash, n.verification_hash, n.repair_hint, n.repair_count, "
            "n.verification_report",
            {"lbl": target},
        )
        if not res.has_next():
            return {}, None, f"label {target!r} not found in KB"
        row = res.get_next()
        target_fields = {
            "target_kind": row[0],
            "statement": row[1],
            "proof": row[2] or "",
            "statement_hash": row[3],
            "verification_hash": row[4],
            "repair_hint": row[5] or "",
            "repair_count": int(row[6]) if row[6] is not None else 0,
            "verification_report": row[7] or "",
        }
        # Dep statement hashes.
        dres = conn.execute(
            "MATCH (n:Node {label: $lbl})-[:DependsOn]->(d:Node) "
            "RETURN d.label, d.statement_hash",
            {"lbl": target},
        )
        deps: dict[str, str] = {}
        while dres.has_next():
            d_label, d_hash = dres.get_next()
            deps[d_label] = d_hash
        target_fields["dep_statement_hashes"] = deps
        return target_fields, target_fields["verification_hash"], None
    finally:
        del conn
        del db


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
