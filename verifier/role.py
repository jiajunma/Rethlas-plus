"""Verifier wrapper entry point (ARCHITECTURE §6.3, PHASE1 M7).

Invocation: ``python -m verifier.role JOB_ID``. The wrapper reads the
dispatched job's context from ``$RETHLAS_WORKSPACE/runtime/jobs/{job_id}.json``,
runs Codex once, parses the final verdict JSON, and atomically publishes a
``verifier.run_completed`` event whose ``verification_hash`` is **exactly**
the ``dispatch_hash`` from the job file (§6.3 hash-match contract).

Static-guarded against importing :mod:`common.kb.kuzu_backend` or
:mod:`librarian.*` (workers stay Kuzu-free per §4.1).
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
from common.runtime.codex_runner import run_codex
from common.runtime.heartbeat import JobHeartbeat
from common.runtime.jobs import (
    STATUS_CRASHED,
    STATUS_PUBLISHING,
    STATUS_RUNNING,
    job_file_path,
    log_path_for,
    read_job_file,
    update_job_file,
)
from verifier.decoder import VerdictParseError, parse_verdict
from verifier.prompt import compose_prompt


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _local_offset_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _resolve_workspace() -> Path:
    raw = os.environ.get("RETHLAS_WORKSPACE")
    if not raw:
        sys.stderr.write("verifier: RETHLAS_WORKSPACE env var is required\n")
        raise SystemExit(2)
    return Path(raw).resolve()


def _publish_verdict(
    *,
    workspace: Path,
    actor: str,
    target: str,
    verdict_payload: dict,
) -> dict:
    alloc = EventIdAllocator()
    eid = alloc.allocate()
    body = {
        "event_id": eid.event_id,
        "type": "verifier.run_completed",
        "actor": actor,
        "ts": _local_offset_iso(),
        "target": target,
        "payload": verdict_payload,
    }
    yyyymmdd = eid.iso_ms[:8]
    date = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    date_dir = workspace / "events" / date
    date_dir.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=eid.iso_ms,
        event_type=body["type"],
        target=target,
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
    state_dir = workspace / "runtime" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "rejected_writes.jsonl"
    entry = {
        "schema": "rethlas-rejection-v1",
        "ts": _utc_now_iso(),
        "actor": actor,
        "event_type_attempted": "verifier.run_completed",
        "target": target,
        "reason": reason,
        "detail": (detail or "")[:1024],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def _heartbeat_interval_s() -> float:
    raw = os.environ.get("RETHLAS_VERIFIER_HEARTBEAT_S")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 60.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verifier-role",
        description="Verifier wrapper (internal — invoked by coordinator).",
    )
    parser.add_argument("job_id")
    parser.add_argument(
        "--codex-argv",
        default="",
        help="Override Codex argv (space-separated); tests pass fake_codex.",
    )
    parser.add_argument(
        "--silent-timeout-s",
        type=float,
        default=1800.0,
    )
    parser.add_argument(
        "--actor",
        default="verifier:codex-default",
    )
    args = parser.parse_args(argv)

    workspace = _resolve_workspace()
    job_path = job_file_path(workspace / "runtime" / "jobs", args.job_id)
    rec = read_job_file(job_path)
    if rec is None:
        sys.stderr.write(f"verifier: job file not found at {job_path}\n")
        return 2
    if rec.kind != "verifier":
        sys.stderr.write(
            f"verifier: job kind {rec.kind!r} is not ``verifier``\n"
        )
        return 2

    update_job_file(job_path, status=STATUS_RUNNING)

    log_path = workspace / rec.log_path if rec.log_path else log_path_for(
        workspace / "runtime" / "logs", args.job_id
    )

    prompt = compose_prompt(rec)
    env = os.environ.copy()
    env.setdefault("RETHLAS_VERIFIER_PROMPT", prompt)

    if args.codex_argv:
        codex_argv = [a for a in args.codex_argv.split(" ") if a]
    else:
        codex_argv = ["codex", "exec", "-m", "auto", "--sandbox", "read-only", prompt]

    with JobHeartbeat(job_path, interval_s=_heartbeat_interval_s()):
        outcome = run_codex(
            argv=codex_argv,
            log_path=log_path,
            silent_timeout_s=args.silent_timeout_s,
            env=env,
        )

    if outcome.timed_out:
        # §6.7.1 step 4: coordinator owns the ``timed_out`` write. Wrapper
        # just exits 124 and lets coordinator detect on its next tick — see
        # _reap_finished_workers in coordinator/main.py. Writing
        # STATUS_CRASHED here would trigger a transient "crashed" state
        # in the dashboard SSE stream before coordinator overwrites it.
        sys.stderr.write("verifier: codex timed out\n")
        return 124
    if outcome.exit_code != 0:
        update_job_file(
            job_path,
            status=STATUS_CRASHED,
            detail=f"codex exit={outcome.exit_code}",
        )
        return outcome.exit_code

    raw = log_path.read_text(encoding="utf-8", errors="replace")
    try:
        verdict = parse_verdict(raw)
    except VerdictParseError as exc:
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
        sys.stderr.write(f"verifier: rejected: {exc}\n")
        return 3

    # §6.3 hash-match: emit the dispatch_hash, NOT what Codex returned.
    payload = {
        "verdict": verdict.verdict,
        "verification_hash": rec.dispatch_hash,
        "verification_report": verdict.verification_report,
        "repair_hint": verdict.repair_hint,
    }
    body = _publish_verdict(
        workspace=workspace,
        actor=args.actor,
        target=rec.target,
        verdict_payload=payload,
    )
    update_job_file(
        job_path,
        status=STATUS_PUBLISHING,
        detail=f"event_id={body['event_id']}",
    )
    sys.stdout.write(f"published {body['event_id']} verdict={verdict.verdict}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
