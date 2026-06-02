"""Referee wrapper entry point for Phase 3 jobs."""

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
from common.phase3.artifacts import event_artifact_hash, write_referee_report_artifact
from common.runtime.codex_runner import run_codex
from common.runtime.heartbeat import JobHeartbeat
from common.runtime.jobs import STATUS_CRASHED, STATUS_PUBLISHING, STATUS_RUNNING
from common.runtime.jobs_v2 import read_role_job_file, update_role_job_file
from common.runtime.jsonl import append_jsonl
from referee.decoder import RefereeDecodeError, parse_referee_report
from referee.prompt import compose_prompt


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
        sys.stderr.write("referee: RETHLAS_WORKSPACE env var is required\n")
        raise SystemExit(2)
    return Path(raw).resolve()


def _publish_report(*, workspace: Path, actor: str, target: str, payload: dict) -> dict:
    alloc = EventIdAllocator()
    eid = alloc.allocate()
    body = {
        "event_id": eid.event_id,
        "type": "referee.review_completed",
        "actor": actor,
        "ts": _local_offset_iso(),
        "target": target,
        "payload": payload,
    }
    yyyymmdd = eid.iso_ms[:8]
    date_dir = workspace / "events" / f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    date_dir.mkdir(parents=True, exist_ok=True)
    fname = format_filename(
        iso_ms=eid.iso_ms,
        event_type=body["type"],
        target=target if ":" in target else None,
        actor=actor,
        seq=eid.seq,
        uid=eid.uid,
    )
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    atomic_write_event(date_dir / fname, raw)
    write_referee_report_artifact(workspace, event_id=eid.event_id, body=body)
    return body


def _record_rejection(
    *, workspace: Path, actor: str, target: str, reason: str, detail: str
) -> None:
    append_jsonl(
        workspace / "runtime" / "state" / "rejected_writes.jsonl",
        {
            "schema": "rethlas-rejection-v1",
            "ts": _utc_now_iso(),
            "actor": actor,
            "event_type_attempted": "referee.review_completed",
            "target": target,
            "reason": reason,
            "detail": detail,
        },
    )


def _report_from_log(log_path: Path):
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    return parse_referee_report(raw)


def _publish_parsed_report(*, workspace: Path, actor: str, target: str, report) -> dict:
    report_dict = report.to_dict()
    payload = report.event_payload(report_hash=event_artifact_hash(report_dict))
    return _publish_report(
        workspace=workspace,
        actor=actor,
        target=report.target if report.target else target,
        payload=payload,
    )


def _try_publish_report_from_log(
    *,
    workspace: Path,
    job_path: Path,
    log_path: Path,
    actor: str,
    target: str,
) -> bool:
    """Publish a complete report already emitted before a Codex transport failure.

    Real Codex runs can disconnect after the model has written a valid JSON
    report into the merged log. Treat that as a completed referee response
    instead of discarding the review as a crashed job.
    """
    try:
        report = _report_from_log(log_path)
    except RefereeDecodeError:
        return False
    body = _publish_parsed_report(
        workspace=workspace,
        actor=actor,
        target=target,
        report=report,
    )
    update_role_job_file(
        job_path,
        status=STATUS_PUBLISHING,
        detail=f"event_id={body['event_id']} salvaged_from_log",
        output_event_id=body["event_id"],
    )
    sys.stdout.write(f"published {body['event_id']} referee_report\n")
    return True


def _heartbeat_interval_s() -> float:
    raw = os.environ.get("RETHLAS_REFEREE_HEARTBEAT_S")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return 60.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="referee-role")
    parser.add_argument("job_id")
    parser.add_argument("--codex-argv", default="")
    parser.add_argument("--silent-timeout-s", type=float, default=1800.0)
    parser.add_argument("--actor", default="referee:codex-default")
    args = parser.parse_args(argv)

    workspace = _resolve_workspace()
    job_path = workspace / "runtime" / "jobs" / f"{args.job_id}.json"
    rec = read_role_job_file(job_path)
    if rec is None:
        sys.stderr.write(f"referee: job file not found at {job_path}\n")
        return 2
    if rec.kind != "referee":
        sys.stderr.write(f"referee: job kind {rec.kind!r} is not referee\n")
        return 2

    update_role_job_file(job_path, status=STATUS_RUNNING)
    log_path = workspace / rec.log_path
    prompt = compose_prompt(rec)
    env = os.environ.copy()
    env.setdefault("RETHLAS_REFEREE_PROMPT", prompt)

    from common.runtime.agents_install import agent_kind_dir

    if args.codex_argv:
        codex_argv = [a for a in args.codex_argv.split(" ") if a]
        codex_cwd = None
    else:
        agent_dir = agent_kind_dir(workspace, "referee")
        if not agent_dir.is_dir():
            detail = "referee agent dir missing in workspace"
            sys.stderr.write(f"referee: {detail}\n")
            update_role_job_file(job_path, status=STATUS_CRASHED, detail=detail)
            return 2
        codex_argv = [
            "codex",
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
            "-C",
            str(agent_dir),
            "--add-dir",
            str(workspace),
            prompt,
        ]
        codex_cwd = agent_dir

    with JobHeartbeat(
        job_path, interval_s=_heartbeat_interval_s(), update_fn=update_role_job_file
    ):
        outcome = run_codex(
            argv=codex_argv,
            log_path=log_path,
            silent_timeout_s=args.silent_timeout_s,
            env=env,
            cwd=codex_cwd,
        )
    if outcome.timed_out:
        if _try_publish_report_from_log(
            workspace=workspace,
            job_path=job_path,
            log_path=log_path,
            actor=args.actor,
            target=rec.target,
        ):
            return 0
        sys.stderr.write("referee: codex timed out\n")
        return 124
    if outcome.exit_code != 0:
        if _try_publish_report_from_log(
            workspace=workspace,
            job_path=job_path,
            log_path=log_path,
            actor=args.actor,
            target=rec.target,
        ):
            return 0
        detail = f"codex exit={outcome.exit_code}"
        update_role_job_file(job_path, status=STATUS_CRASHED, detail=detail)
        return outcome.exit_code

    try:
        report = _report_from_log(log_path)
    except RefereeDecodeError as exc:
        _record_rejection(
            workspace=workspace,
            actor=args.actor,
            target=rec.target,
            reason=exc.reason,
            detail=exc.detail,
        )
        update_role_job_file(
            job_path, status=STATUS_CRASHED, reason=exc.reason, detail=exc.detail
        )
        sys.stderr.write(f"referee: rejected: {exc}\n")
        return 3

    body = _publish_parsed_report(
        workspace=workspace,
        actor=args.actor,
        target=rec.target,
        report=report,
    )
    update_role_job_file(
        job_path,
        status=STATUS_PUBLISHING,
        detail=f"event_id={body['event_id']}",
        output_event_id=body["event_id"],
    )
    sys.stdout.write(f"published {body['event_id']} referee_report\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
