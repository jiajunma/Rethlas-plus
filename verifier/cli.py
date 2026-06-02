"""``rethlas verifier`` — standalone verifier dispatch (PHASE1 M7 system test).

Mirrors :mod:`generator.cli`: read target context from the workspace KB,
write a JobRecord, invoke role.main inline. Phase I verifier mode is
fixed to ``"single"``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli.workspace import ensure_initialised, workspace_paths
from common.runtime.jobs import (
    JobRecord,
    STATUS_STARTING,
    job_file_path,
    make_job_id,
    utc_now_iso,
    write_job_file,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rethlas verifier",
        description="Run a verifier attempt against the local workspace.",
    )
    p.add_argument("--target", required=True)
    p.add_argument("--codex-argv", default="")
    p.add_argument("--silent-timeout-s", type=float, default=1800.0)
    p.add_argument("--actor", default="verifier:cli")
    p.add_argument("--workspace", default=None)
    return p


def _read_target_context(ws_root: Path, target: str) -> tuple[dict, str | None]:
    # §13: read the target straight from the markdown KB (no Kuzu). The target
    # is typically a staged node (pass_count <= 0) awaiting verification — all
    # nodes live in nodes/*.md, so the reader finds it by label.
    from common.kb.markdown_reader import read_nodes_dir

    nodes_dir = ws_root / "knowledge_base" / "nodes"
    if not nodes_dir.is_dir():
        return {}, "knowledge_base/nodes does not exist"
    nodes = read_nodes_dir(nodes_dir)
    node = nodes.get(target)
    if node is None:
        return {}, f"label {target!r} not found in KB"
    deps = {
        dep: (nodes[dep].statement_hash if dep in nodes else "")
        for dep in node.depends_on
    }
    return {
        "target_kind": node.kind.value,
        "statement": node.statement,
        "proof": node.proof or "",
        "statement_hash": node.statement_hash,
        "verification_hash": node.verification_hash,
        "dep_statement_hashes": deps,
    }, None


def run_verifier(workspace: str | None, args: argparse.Namespace) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)

    fields, err = _read_target_context(ws.root, args.target)
    if err is not None:
        sys.stderr.write(f"verifier: {err}\n")
        return 2

    iso_ms_dt = utc_now_iso()
    now = datetime.now(tz=timezone.utc)
    iso_ms = now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}"

    import secrets
    uid = secrets.token_hex(8)
    job_id = make_job_id("verifier", iso_ms=iso_ms, uid=uid)
    log_rel = f"runtime/logs/{job_id}.codex.log"

    rec = JobRecord(
        job_id=job_id,
        kind="verifier",
        target=args.target,
        mode="single",
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
    )
    write_job_file(job_file_path(ws.runtime_jobs, job_id), rec)

    os.environ["RETHLAS_WORKSPACE"] = str(ws.root)
    from verifier.role import main as role_main
    role_args = [job_id]
    if args.codex_argv:
        role_args.extend(["--codex-argv", args.codex_argv])
    role_args.extend(["--silent-timeout-s", str(args.silent_timeout_s)])
    role_args.extend(["--actor", args.actor])
    return role_main(role_args)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_verifier(args.workspace, args)


__all__ = ["main", "run_verifier"]
