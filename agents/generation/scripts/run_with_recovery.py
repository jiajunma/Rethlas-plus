#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import atexit
import re
import signal
import subprocess
import sys
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.codex_budget import acquire_slot, note_rate_limit, release_slot  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "results"
LOGS_ROOT = REPO_ROOT / "logs"
MEMORY_ROOT = REPO_ROOT / "memory"
SECTION_VERIFY = REPO_ROOT / "scripts" / "verify_sections.py"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_problem_id(raw: str) -> str:
    cleaned = re.sub(r"\s+", "_", raw.strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "problem"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--problem-file", default="data/example.md")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning-effort", default="xhigh")
    parser.add_argument("--verify-url", default="http://127.0.0.1:8091/health")
    parser.add_argument("--backoff-seconds", type=int, default=30)
    parser.add_argument("--attempt-timeout-seconds", type=int, default=1800)
    parser.add_argument("--section-verify-timeout-seconds", type=int, default=1200)
    parser.add_argument("--max-attempts", type=int, default=0, help="0 means unlimited")
    parser.add_argument("--extra-prompt", default="")
    return parser.parse_args()


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(lock_path: Path) -> None:
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        old_pid = payload.get("pid")
        if isinstance(old_pid, int) and process_alive(old_pid):
            raise RuntimeError(f"run_with_recovery already active with pid={old_pid}")
        lock_path.unlink(missing_ok=True)

    payload = {
        "pid": os.getpid(),
        "created_at_utc": utc_now(),
    }
    write_json(lock_path, payload)

    def _cleanup() -> None:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            current = {}
        if current.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)

    atexit.register(_cleanup)


def verification_service_ok(verify_url: str) -> bool:
    try:
        r = requests.get(verify_url, timeout=3)
        return r.ok
    except Exception:  # noqa: BLE001
        return False


def classify_failure(log_text: str) -> str:
    lowered = log_text.lower()
    if "429" in log_text or "too many requests" in lowered or "rate limit" in lowered:
        return "rate_limit"
    if "verification service is not reachable" in lowered:
        return "verifier_unreachable"
    if "timed out" in lowered:
        return "timeout"
    if "500 internal server error" in lowered or "server error" in lowered:
        return "server_error"
    return "unknown"


def build_prompt(
    args: argparse.Namespace,
    problem_id: str,
    repair_brief_path: Path,
    suspect_claims_path: Path,
    base_extra_prompt: str,
) -> str:
    prompt = (
        f"Use AGENTS.md exactly to solve the math problem in {args.problem_file}. "
        f"If memory/{problem_id}/ or results/{problem_id}/ already contain artifacts from an earlier run, "
        "resume from them instead of restarting from scratch. Preserve and build on prior memory, failed paths, and proof drafts."
    )
    if repair_brief_path.exists():
        prompt += (
            "\n\nCurrent repair brief from the latest verification failure:\n"
            + repair_brief_path.read_text(encoding="utf-8")
            + "\nUse it explicitly to prioritize the next repair."
        )
    if suspect_claims_path.exists():
        prompt += (
            "\n\nRepeated-failure suspect claims:\n"
            + suspect_claims_path.read_text(encoding="utf-8")
            + "\nIf one of these claims appears false, do not keep patching blindly; either revise the statement or produce counterexample evidence."
        )
    if base_extra_prompt.strip():
        prompt += " " + base_extra_prompt.strip()
    return prompt


def latest_memory_verification_record(memory_verification_path: Path) -> Optional[Dict[str, Any]]:
    if not memory_verification_path.exists():
        return None
    lines = memory_verification_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload.get("record") if isinstance(payload.get("record"), dict) else payload
    return None


def build_repair_brief(
    section_report: Path,
    memory_verification_path: Path,
) -> Optional[Dict[str, Any]]:
    if section_report.exists():
        payload = read_json(section_report)
        if payload:
            structure = payload.get("structure_report") or {}
            issues = structure.get("issues") or []
            pass_reports = payload.get("pass_reports") or []
            latest = pass_reports[-1] if pass_reports else {}
            section_reports = latest.get("section_reports") or []
            failing = next((r for r in section_reports if r.get("verdict") not in {None, "correct"}), None)
            if issues or failing:
                brief: Dict[str, Any] = {
                    "created_at_utc": utc_now(),
                    "source": "section_verification",
                    "scope": "structure" if issues else "section",
                    "failing_locations": [],
                    "summary": structure.get("summary") or latest.get("overall_verdict") or "Section verification failed.",
                    "critical_errors": [],
                    "gaps": [],
                    "repair_hints": "",
                    "next_actions": [],
                }
                if issues:
                    brief["failing_locations"] = [item.get("location", "") for item in issues]
                    brief["gaps"] = [{"location": item.get("location", ""), "issue": item.get("issue", "")} for item in issues]
                    brief["repair_hints"] = "Repair the structure issues first, then rerun section verification."
                    brief["next_actions"] = ["Fix the failing structure items", "Rerun section verification from pass 1"]
                elif failing:
                    report = (failing.get("verification") or {}).get("verification_report") or {}
                    brief["failing_locations"] = [failing.get("title", "")]
                    brief["summary"] = report.get("summary") or failing.get("error") or "Section verification failed."
                    brief["critical_errors"] = report.get("critical_errors") or []
                    brief["gaps"] = report.get("gaps") or []
                    brief["repair_hints"] = (failing.get("verification") or {}).get("repair_hints") or failing.get("error", "")
                    brief["next_actions"] = ["Repair the failing block", "Rerun section verification from pass 1"]
                return brief

    latest_verification = latest_memory_verification_record(memory_verification_path)
    if latest_verification and latest_verification.get("verdict") == "wrong":
        verification_report = latest_verification.get("verification_report") or {}
        return {
            "created_at_utc": utc_now(),
            "source": "verification_reports_memory",
            "scope": "full_proof",
            "failing_locations": [
                item.get("location", "")
                for item in (verification_report.get("critical_errors") or []) + (verification_report.get("gaps") or [])
                if isinstance(item, dict)
            ],
            "summary": latest_verification.get("summary") or verification_report.get("summary") or "Full verification failed.",
            "critical_errors": verification_report.get("critical_errors") or [],
            "gaps": verification_report.get("gaps") or [],
            "repair_hints": latest_verification.get("repair_hints") or "",
            "next_actions": ["Repair the reported issues", "Rerun section verification and then full verification"],
        }
    return None


def update_suspect_claims(
    suspect_claims_path: Path,
    repair_brief: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    existing = read_json(suspect_claims_path) if suspect_claims_path.exists() else {}
    claims: Dict[str, Dict[str, Any]] = existing if isinstance(existing, dict) else {}
    if repair_brief:
        for item in (repair_brief.get("critical_errors") or []) + (repair_brief.get("gaps") or []):
            if not isinstance(item, dict):
                continue
            location = str(item.get("location", "")).strip()
            if not location:
                continue
            entry = claims.setdefault(
                location,
                {
                    "location": location,
                    "count": 0,
                    "kinds_seen": [],
                    "last_summary": "",
                    "latest_hint": "",
                    "status": "monitor",
                },
            )
            entry["count"] += 1
            kind = "critical_error" if item in (repair_brief.get("critical_errors") or []) else "gap"
            if kind not in entry["kinds_seen"]:
                entry["kinds_seen"].append(kind)
            entry["last_summary"] = repair_brief.get("summary", "")
            entry["latest_hint"] = repair_brief.get("repair_hints", "")
            if entry["count"] >= 4:
                entry["status"] = "suspected_false"
            elif entry["count"] >= 2:
                entry["status"] = "repeated_failure"
    write_json(suspect_claims_path, claims)
    return list(claims.values())


def snapshot_attempt_artifacts(
    snapshots_dir: Path,
    attempt_num: int,
    blueprint: Path,
    section_report: Path,
    repair_brief_path: Path,
) -> None:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    if blueprint.exists():
        shutil.copy2(blueprint, snapshots_dir / f"attempt{attempt_num:02d}_blueprint.md")
    if section_report.exists():
        shutil.copy2(section_report, snapshots_dir / f"attempt{attempt_num:02d}_section_verification.json")
    if repair_brief_path.exists():
        shutil.copy2(repair_brief_path, snapshots_dir / f"attempt{attempt_num:02d}_repair_brief.json")


def pause_for_rate_limit(state: Dict[str, Any], state_path: Path, heartbeat_path: Path) -> int:
    state["status"] = "paused_rate_limit"
    state["updated_at_utc"] = utc_now()
    write_json(state_path, state)
    while True:
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        time.sleep(60)


def run_monitored_subprocess(
    cmd: List[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int,
    heartbeat_path: Path,
    state_path: Path,
    state: Dict[str, Any],
    phase: str,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"started_at_utc: {utc_now()}\n")
        handle.write(f"phase: {phase}\n")
        handle.write(f"command: {' '.join(cmd)}\n\n")
        handle.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        start = time.monotonic()
        while True:
            heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
            state["updated_at_utc"] = utc_now()
            state["current_phase"] = phase
            write_json(state_path, state)
            try:
                return proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if time.monotonic() - start >= timeout_seconds:
                    handle.write(f"\n[runner] {phase} timed out after {timeout_seconds} seconds\n")
                    handle.flush()
                    os.killpg(proc.pid, signal.SIGINT)
                    try:
                        return proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        return proc.wait()


def run_attempt(
    args: argparse.Namespace,
    problem_id: str,
    attempt_num: int,
    log_file: Path,
    prompt: str,
    heartbeat_path: Path,
    state_path: Path,
    state: Dict[str, Any],
) -> int:
    cmd = [
        "codex",
        "exec",
        "-C",
        str(REPO_ROOT),
        "-m",
        args.model,
        "--config",
        f'model_reasoning_effort="{args.reasoning_effort}"',
        "--dangerously-bypass-approvals-and-sandbox",
        prompt,
    ]

    with log_file.open("w", encoding="utf-8") as handle:
        handle.write(f"started_at_utc: {utc_now()}\n")
        handle.write(f"attempt: {attempt_num}\n")
        handle.write(f"command: {' '.join(cmd)}\n\n")
        handle.flush()
        slot_path = acquire_slot(f"generator:{problem_id}:attempt{attempt_num}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=REPO_ROOT,
                stdout=handle,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            start = time.monotonic()
            while True:
                heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
                state["updated_at_utc"] = utc_now()
                write_json(state_path, state)
                try:
                    return proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if time.monotonic() - start >= args.attempt_timeout_seconds:
                        handle.write(f"\n[runner] attempt timed out after {args.attempt_timeout_seconds} seconds\n")
                        handle.flush()
                        os.killpg(proc.pid, signal.SIGINT)
                        try:
                            return proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            return proc.wait()
        finally:
            release_slot(slot_path)


def main() -> int:
    args = parse_args()
    problem_path = (REPO_ROOT / args.problem_file).resolve()
    if not problem_path.exists():
        print(f"Problem file not found: {problem_path}", file=sys.stderr)
        return 1

    problem_rel = args.problem_file.removeprefix("data/").removesuffix(".md")
    problem_id = sanitize_problem_id(problem_path.stem)
    results_dir = RESULTS_ROOT / problem_id
    logs_dir = LOGS_ROOT / problem_rel
    state_path = results_dir / "run_state.json"
    lock_path = results_dir / "run_lock.json"
    heartbeat_path = results_dir / "heartbeat.txt"
    verified_blueprint = results_dir / "blueprint_verified.md"
    blueprint = results_dir / "blueprint.md"
    section_report = results_dir / "section_verification.json"
    stale_section_report = results_dir / "section_verification.stale_preclean.json"
    repair_brief_path = results_dir / "repair_brief.json"
    suspect_claims_path = results_dir / "suspect_claims.json"
    loop_journal_path = results_dir / "loop_journal.jsonl"
    snapshots_dir = results_dir / "attempt_snapshots"
    memory_verification_path = MEMORY_ROOT / problem_id / "verification_reports.jsonl"

    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    acquire_lock(lock_path)

    state = read_json(state_path)
    state.setdefault("problem_id", problem_id)
    state.setdefault("problem_file", args.problem_file)
    state.setdefault("created_at_utc", utc_now())
    state.setdefault("attempts", [])
    base_extra_prompt = args.extra_prompt

    if verified_blueprint.exists():
        state["status"] = "verified"
        state["verified_blueprint"] = str(verified_blueprint)
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        print(f"Verified blueprint already present: {verified_blueprint}")
        return 0

    attempt_num = len(state["attempts"]) + 1
    while args.max_attempts == 0 or attempt_num <= args.max_attempts:
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        state["status"] = "running"
        state["active_attempt"] = attempt_num
        state["runner_pid"] = os.getpid()
        state["current_phase"] = "generation"
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        append_jsonl(
            loop_journal_path,
            {
                "timestamp_utc": utc_now(),
                "event": "attempt_started",
                "attempt": attempt_num,
            },
        )

        if not verification_service_ok(args.verify_url):
            state["status"] = "blocked_verifier"
            state["updated_at_utc"] = utc_now()
            state["last_failure_type"] = "verifier_unreachable"
            write_json(state_path, state)
            time.sleep(args.backoff_seconds)
            continue

        log_file = logs_dir / f"{problem_id}-attempt{attempt_num:02d}.md"
        state["current_log"] = str(log_file)
        state["attempt_started_at_utc"] = utc_now()
        if section_report.exists():
            try:
                section_report.replace(stale_section_report)
            except Exception:  # noqa: BLE001
                pass
        prompt = build_prompt(args, problem_id, repair_brief_path, suspect_claims_path, base_extra_prompt)
        write_json(state_path, state)
        exit_code = run_attempt(args, problem_id, attempt_num, log_file, prompt, heartbeat_path, state_path, state)
        log_text = log_file.read_text(encoding="utf-8", errors="ignore")
        failure_type = classify_failure(log_text) if exit_code != 0 else ""

        state["attempts"].append(
            {
                "attempt": attempt_num,
                "started_log": str(log_file),
                "exit_code": exit_code,
                "failure_type": failure_type,
                "finished_at_utc": utc_now(),
            }
        )
        state["updated_at_utc"] = utc_now()

        if blueprint.exists():
            try:
                section_verify_log = results_dir / "section_verify_run.log"
                state["current_phase"] = "section_verifying"
                write_json(state_path, state)
                section_exit = run_monitored_subprocess(
                    [sys.executable, str(SECTION_VERIFY), str(blueprint)],
                    cwd=REPO_ROOT,
                    log_path=section_verify_log,
                    timeout_seconds=args.section_verify_timeout_seconds,
                    heartbeat_path=heartbeat_path,
                    state_path=state_path,
                    state=state,
                    phase="section_verifying",
                )
                if section_exit != 0 and not section_report.exists():
                    failure_type = failure_type or "section_verifier_failed"
            except Exception:  # noqa: BLE001
                pass

        repair_brief = build_repair_brief(section_report, memory_verification_path)
        if repair_brief:
            write_json(repair_brief_path, repair_brief)
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "repair_brief_written",
                    "attempt": attempt_num,
                    "repair_brief": repair_brief,
                },
            )
        suspect_claims = update_suspect_claims(suspect_claims_path, repair_brief)
        if suspect_claims:
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "suspect_claims_updated",
                    "attempt": attempt_num,
                    "suspect_claims": suspect_claims,
                },
            )
        snapshot_attempt_artifacts(snapshots_dir, attempt_num, blueprint, section_report, repair_brief_path)
        append_jsonl(
            loop_journal_path,
            {
                "timestamp_utc": utc_now(),
                "event": "attempt_finished",
                "attempt": attempt_num,
                "exit_code": exit_code,
                "failure_type": failure_type,
                "log_file": str(log_file),
            },
        )

        if verified_blueprint.exists():
            state["status"] = "verified"
            state["verified_blueprint"] = str(verified_blueprint)
            write_json(state_path, state)
            heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
            print(f"Verified blueprint written to {verified_blueprint}")
            return 0

        state["status"] = "retrying"
        state["last_failure_type"] = failure_type
        state["working_blueprint"] = str(blueprint) if blueprint.exists() else ""
        state["section_verification"] = str(section_report) if section_report.exists() else ""
        state.pop("attempt_started_at_utc", None)
        state["current_phase"] = "idle"
        write_json(state_path, state)
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")

        if failure_type == "rate_limit":
            note_rate_limit()
            return pause_for_rate_limit(state, state_path, heartbeat_path)

        attempt_num += 1
        time.sleep(args.backoff_seconds)

    state["status"] = "exhausted"
    state["updated_at_utc"] = utc_now()
    write_json(state_path, state)
    heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
