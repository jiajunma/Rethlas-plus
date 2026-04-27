"""Generator wrapper entry point (ARCHITECTURE §6.2, PHASE1 M6).

Invocation: ``python -m generator.role JOB_ID``. The wrapper reads the
full dispatch context from ``$RETHLAS_WORKSPACE/runtime/jobs/{job_id}.json``
(populated by the coordinator), runs Codex via :mod:`common.runtime.codex_runner`,
parses stdout via :mod:`generator.decoder`, and atomically publishes a
``generator.batch_committed`` event into ``events/``.

This module **must not import** :mod:`common.kb.kuzu_backend` or
:mod:`librarian.*` per §4.1 — the static guard in
``tests/unit/test_m5_static_kuzu_free.py`` enforces this.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from common.events.filenames import format_filename
from common.events.ids import EventIdAllocator
from common.events.io import atomic_write_event
from common.runtime.jsonl import append_jsonl
from common.runtime.codex_runner import run_codex
from common.runtime.heartbeat import JobHeartbeat
from common.runtime.jobs import (
    JobRecord,
    STATUS_CRASHED,
    STATUS_PUBLISHING,
    STATUS_RUNNING,
    job_file_path,
    log_path_for,
    read_job_file,
    update_job_file,
)
from generator.decoder import (
    DecodeError,
    StagedBatch,
    decode_codex_stdout,
)
from generator.prompt import compose_prompt


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _local_offset_iso() -> str:
    local = datetime.now().astimezone()
    return local.isoformat(timespec="milliseconds")


def _resolve_workspace() -> Path:
    raw = os.environ.get("RETHLAS_WORKSPACE")
    if not raw:
        sys.stderr.write("generator: RETHLAS_WORKSPACE env var is required\n")
        raise SystemExit(2)
    return Path(raw).resolve()


def _read_nodes_dir_view(nodes_dir: Path) -> dict[str, str]:
    """Walk ``nodes/*.md``; return a ``{label: statement_hash}`` map.

    The mapping powers the decoder's ``existing_label_present`` /
    ``existing_dep_hash`` callbacks. If a file is missing or malformed
    we skip it — the decoder will simply see the dep as unresolved,
    which is the correct behaviour at the worker boundary.
    """
    import yaml

    view: dict[str, str] = {}
    if not nodes_dir.is_dir():
        return view
    for path in nodes_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        try:
            _, fm, _ = text.split("---", 2)
        except ValueError:
            continue
        try:
            data = yaml.safe_load(fm)
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        label = data.get("label")
        sh = data.get("statement_hash")
        if isinstance(label, str) and isinstance(sh, str):
            view[label] = sh
    return view


def _publish_batch(
    *,
    workspace: Path,
    actor: str,
    batch: StagedBatch,
) -> dict:
    """Compose the truth event body and atomically write it to ``events/``."""
    alloc = EventIdAllocator()
    eid = alloc.allocate()
    body = {
        "event_id": eid.event_id,
        "type": "generator.batch_committed",
        "actor": actor,
        "ts": _local_offset_iso(),
        "target": batch.target,
        "payload": {
            "attempt_id": f"gen-{eid.event_id}",
            "target": batch.target,
            "mode": batch.mode,
            "nodes": [
                {
                    "label": n.label,
                    "kind": n.kind.value,
                    "statement": n.statement,
                    "proof": n.proof,
                    "remark": n.remark,
                    "source_note": n.source_note,
                }
                for n in batch.nodes
            ],
        },
    }
    yyyymmdd = eid.iso_ms[:8]
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    date_dir = workspace / "events" / date
    date_dir.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=eid.iso_ms,
        event_type=body["type"],
        target=batch.target,
        actor=actor,
        seq=eid.seq,
        uid=eid.uid,
    )
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    atomic_write_event(date_dir / fname, raw)
    return body


def _record_rejection(
    *,
    workspace: Path,
    actor: str,
    target: str,
    reason: str,
    detail: str,
) -> None:
    """Append a single line to ``runtime/state/rejected_writes.jsonl``."""
    state_dir = workspace / "runtime" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "rejected_writes.jsonl"
    entry = {
        "schema": "rethlas-rejection-v1",
        "ts": _utc_now_iso(),
        "actor": actor,
        "event_type_attempted": "generator.batch_committed",
        "target": target,
        "reason": reason,
        "detail": detail or "",
    }
    append_jsonl(path, entry)


def _heartbeat_interval_s() -> float:
    raw = os.environ.get("RETHLAS_GENERATOR_HEARTBEAT_S")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 60.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generator-role",
        description="Generator wrapper (internal — invoked by coordinator).",
    )
    parser.add_argument("job_id")
    parser.add_argument(
        "--codex-argv",
        default="",
        help=(
            "Override Codex argv (space-separated). Tests use this to swap "
            "``codex exec`` for ``tests/fixtures/fake_codex.py``."
        ),
    )
    parser.add_argument(
        "--silent-timeout-s",
        type=float,
        default=1800.0,
        help="codex_silent_timeout_seconds (§7.4)",
    )
    parser.add_argument(
        "--actor",
        default="generator:codex-default",
        help="actor name for emitted events",
    )
    args = parser.parse_args(argv)

    workspace = _resolve_workspace()
    job_path = job_file_path(workspace / "runtime" / "jobs", args.job_id)
    rec = read_job_file(job_path)
    if rec is None:
        sys.stderr.write(f"generator: job file not found at {job_path}\n")
        return 2
    if rec.kind != "generator":
        sys.stderr.write(
            f"generator: job kind {rec.kind!r} is not ``generator``\n"
        )
        return 2

    update_job_file(job_path, status=STATUS_RUNNING)

    log_path = workspace / rec.log_path if rec.log_path else log_path_for(
        workspace / "runtime" / "logs", args.job_id
    )

    # Compose prompt. We pass it via the FAKE_CODEX_PROMPT env var so
    # tests can ignore it; real Codex would receive it as the
    # positional ``prompt`` argument.
    prompt = compose_prompt(rec)
    env = os.environ.copy()
    env.setdefault("RETHLAS_GENERATOR_PROMPT", prompt)

    # H22: codex must run from the workspace-local agent dir so it loads
    # the Phase I AGENTS.md / .codex/config.toml / skill set materialized
    # by ``rethlas init`` (see common/runtime/agents_install.py). With
    # ``-C agent_dir`` and ``--add-dir workspace`` the agent's working
    # tree is bounded to two known paths under the workspace, so a stray
    # ``rg --files ..`` cannot escape into the user's home dir or a
    # sibling project. Test paths set ``--codex-argv`` explicitly and
    # bypass this default.
    from common.runtime.agents_install import agent_kind_dir

    agent_dir = agent_kind_dir(workspace, "generation")
    codex_cwd: Path | None
    if args.codex_argv:
        codex_argv = [a for a in args.codex_argv.split(" ") if a]
        codex_cwd = None
    else:
        if not agent_dir.is_dir():
            sys.stderr.write(
                f"generator: agent dir {agent_dir} missing — "
                "run `rethlas init` against this workspace before dispatching\n"
            )
            update_job_file(
                job_path,
                status=STATUS_CRASHED,
                detail="generator agent dir missing in workspace",
            )
            return 2
        codex_argv = [
            "codex",
            "exec",
            "-m",
            "auto",
            "--sandbox",
            "read-only",
            "-C",
            str(agent_dir),
            "--add-dir",
            str(workspace),
            prompt,
        ]
        codex_cwd = agent_dir

    with JobHeartbeat(job_path, interval_s=_heartbeat_interval_s()):
        outcome = run_codex(
            argv=codex_argv,
            log_path=log_path,
            silent_timeout_s=args.silent_timeout_s,
            env=env,
            cwd=codex_cwd,
        )

    if outcome.timed_out:
        # Coordinator owns timed_out write per §6.7.1; wrapper just
        # exits 124 and lets coordinator detect on its next tick (see
        # _reap_finished_workers in coordinator/main.py). Writing a
        # transient STATUS_CRASHED here would briefly mislabel the job
        # in the dashboard SSE stream before coordinator's override.
        sys.stderr.write("generator: codex timed out\n")
        return 124
    if outcome.exit_code != 0:
        update_job_file(
            job_path,
            status=STATUS_CRASHED,
            detail=f"codex exit={outcome.exit_code}",
        )
        return outcome.exit_code

    raw = log_path.read_text(encoding="utf-8", errors="replace")
    nodes_view = _read_nodes_dir_view(workspace / "knowledge_base" / "nodes")

    try:
        batch = decode_codex_stdout(
            raw,
            target=rec.target,
            mode=rec.mode,
            h_rejected=rec.h_rejected or None,
            existing_label_present=lambda lbl: lbl in nodes_view,
            existing_dep_hash=lambda lbl: nodes_view.get(lbl),
        )
    except DecodeError as exc:
        _record_rejection(
            workspace=workspace,
            actor=args.actor,
            target=rec.target,
            reason=exc.reason,
            detail=exc.detail,
        )
        update_job_file(
            job_path,
            status=STATUS_CRASHED,
            reason=exc.reason,
            detail=exc.detail,
        )
        sys.stderr.write(f"generator: rejected: {exc}\n")
        return 3

    body = _publish_batch(workspace=workspace, actor=args.actor, batch=batch)
    update_job_file(
        job_path,
        status=STATUS_PUBLISHING,
        detail=f"event_id={body['event_id']}",
    )
    sys.stdout.write(f"published {body['event_id']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
