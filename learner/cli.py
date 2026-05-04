"""``rethlas learner`` standalone Phase 3 dispatch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli.workspace import ensure_initialised, workspace_paths
from common.runtime.jobs import STATUS_STARTING, job_file_path, utc_now_iso
from common.runtime.jobs_v2 import (
    RoleJobRecord,
    make_role_job_id,
    write_role_job_file,
)
from common.runtime.role_queue import enqueue_role_item


def _iso_ms_now() -> str:
    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y%m%dT%H%M%S.") + f"{now.microsecond // 1000:03d}"


def _hash_input(data: dict[str, Any]) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise SystemExit(f"context file must contain a JSON object: {path}")
    return parsed


def run_learner(workspace: str | None, args: argparse.Namespace) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)

    input_packet = _load_json(args.context_json)
    source_id = args.source or input_packet.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        sys.stderr.write("learner: --source or context_json.source_id is required\n")
        return 2
    input_packet.setdefault("source_id", source_id)
    input_packet.setdefault("source_spans", [])
    input_packet.setdefault("existing_kb_matches", [])
    input_packet.setdefault("budgets", {"max_nodes": args.max_nodes})

    if getattr(args, "queue", False):
        item = enqueue_role_item(
            ws.root,
            kind="learner",
            mode="learn_source_spans",
            target=f"{source_id}#spans",
            input_packet=input_packet,
        )
        sys.stdout.write(f"queued {item.queue_id}\n")
        return 0

    iso_ms = _iso_ms_now()
    uid = secrets.token_hex(8)
    job_id = make_role_job_id("learner", iso_ms=iso_ms, uid=uid)
    started = utc_now_iso()
    context_hash = _hash_input(input_packet)
    log_rel = f"runtime/logs/{job_id}.codex.log"

    rec = RoleJobRecord(
        job_id=job_id,
        kind="learner",
        mode="learn_source_spans",
        target=f"{source_id}#spans",
        context_hash=context_hash,
        dispatch_hash=context_hash,
        pid=os.getpid(),
        pgid=os.getpid(),
        started_at=started,
        updated_at=started,
        status=STATUS_STARTING,
        log_path=log_rel,
        input=input_packet,
    )
    write_role_job_file(job_file_path(ws.runtime_jobs, job_id), rec)

    os.environ["RETHLAS_WORKSPACE"] = str(ws.root)
    from learner.role import main as role_main

    role_args = [job_id]
    if args.codex_argv:
        role_args.extend(["--codex-argv", args.codex_argv])
    role_args.extend(["--silent-timeout-s", str(args.silent_timeout_s)])
    role_args.extend(["--actor", args.actor])
    return role_main(role_args)


__all__ = ["run_learner"]
