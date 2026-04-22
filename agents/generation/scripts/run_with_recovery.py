#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import atexit
import hashlib
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
from common.codex_budget import acquire_slot, maybe_restore_default_limit, note_rate_limit, release_slot  # noqa: E402
from verification_aggregation import (  # noqa: E402
    build_current_scheduler_view,
    latest_current_wrong_from_scheduler_view,
    refresh_verification_cache_from_results,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO_ROOT / "results"
LOGS_ROOT = REPO_ROOT / "logs"
MEMORY_ROOT = REPO_ROOT / "memory"
SECTION_VERIFY = REPO_ROOT / "scripts" / "verify_sections.py"
THEOREM_LIBRARY_LINT = REPO_ROOT / "scripts" / "lint_theorem_library.py"
VERIFIER_RESULTS_ROOT = REPO_ROOT.parent / "verification" / "results"
LABEL_PATTERN = re.compile(r"(lem:[A-Za-z0-9_]+|prop:[A-Za-z0-9_]+|thm:[A-Za-z0-9_]+)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execution_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
    parser.add_argument("--section-verify-timeout-seconds", type=int, default=0)
    parser.add_argument("--section-verify-mode", choices=("sequential", "parallel"), default="parallel")
    parser.add_argument("--section-verify-max-workers", type=int, default=3)
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


def cleanup_orphan_generator_codex() -> None:
    completed = subprocess.run(
        [
            "/bin/zsh",
            "-lc",
            "ps -axo pid,ppid,command | awk '/codex exec -C / && /agents\\/generation/ && $2 == 1 {print $1}'",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    for raw in completed.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            os.kill(int(raw), signal.SIGTERM)
        except Exception:
            pass


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


def fetch_verifier_health(verify_url: str) -> Dict[str, Any]:
    target = verify_url if verify_url.endswith("/health") else verify_url.rstrip("/") + "/health"
    try:
        r = requests.get(target, timeout=3)
        if not r.ok:
            return {}
        payload = r.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


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


def latest_section_snapshot(section_report_path: Path) -> Dict[str, Any]:
    payload = read_json(section_report_path) if section_report_path.exists() else {}
    if not payload:
        return {}
    pass_reports = payload.get("pass_reports") or []
    latest = pass_reports[-1] if isinstance(pass_reports, list) and pass_reports else {}
    if not isinstance(latest, dict):
        latest = {}
    session_summary = latest.get("session_summary") or {}
    block_states = latest.get("block_states") or []
    if not isinstance(block_states, list):
        block_states = []
    states_by_title = {
        str(item.get("title")): item
        for item in block_states
        if isinstance(item, dict) and item.get("title")
    }
    return {
        "overall_verdict": payload.get("overall_verdict"),
        "passes_completed": payload.get("passes_completed"),
        "passes_required": payload.get("passes_required"),
        "session_summary": session_summary if isinstance(session_summary, dict) else {},
        "states_by_title": states_by_title,
        "latest_pass": latest,
    }


def active_section_blueprint_path(state: Dict[str, Any], blueprint_path: Path, section_blueprint_path: Path) -> Path:
    if str(state.get("current_phase") or "") == "section_verifying" and section_blueprint_path.exists():
        return section_blueprint_path
    return blueprint_path


def should_resume_section_verification(section_report_path: Path) -> bool:
    snapshot = latest_section_snapshot(section_report_path)
    if not snapshot:
        return False
    session_summary = snapshot.get("session_summary") or {}
    ready = int(session_summary.get("ready", 0) or 0)
    provisional = int(session_summary.get("provisional", 0) or 0)
    wrong_roots = int(session_summary.get("wrong_roots", 0) or 0)
    invalidated = int(session_summary.get("invalidated", 0) or 0)
    if wrong_roots == 0 and (ready > 0 or provisional > 0 or invalidated > 0):
        return True
    states_by_title = snapshot.get("states_by_title") or {}
    statuses = [
        str(item.get("scheduler_status") or "")
        for item in states_by_title.values()
        if isinstance(item, dict)
    ]
    return "wrong" not in statuses and any(status in {"ready", "provisional", "invalidated"} for status in statuses)


def should_resume_section_verification_from_current_view(
    blueprint_path: Path,
    theorem_library_path: Path,
    verification_cache_path: Path,
) -> bool:
    scheduler_view = build_current_scheduler_view(
        blueprint_path=blueprint_path,
        theorem_library_path=theorem_library_path,
        verification_cache_path=verification_cache_path,
    )
    blocks = scheduler_view.get("blocks") or []
    if any(block.get("scheduler_status") == "wrong" for block in blocks if isinstance(block, dict)):
        return False
    return any(
        block.get("scheduler_status") in {"ready", "provisional"}
        for block in blocks
        if isinstance(block, dict)
    )


def split_top_level_blocks(text: str) -> List[str]:
    lines = text.splitlines()
    starts: List[int] = []
    for i, line in enumerate(lines):
        if line.startswith("# "):
            starts.append(i)
    if not starts:
        return []
    starts.append(len(lines))
    return ["\n".join(lines[a:b]).strip() + "\n" for a, b in zip(starts, starts[1:])]


def extract_blueprint_block(block: str) -> Dict[str, Any]:
    lines = block.splitlines()
    title = lines[0].strip() if lines else ""
    statement_match = re.search(r"^## statement\s*$", block, flags=re.MULTILINE)
    proof_match = re.search(r"^## proof\s*$", block, flags=re.MULTILINE)
    statement = ""
    proof = ""
    if statement_match and proof_match and statement_match.end() <= proof_match.start():
        statement = block[statement_match.end():proof_match.start()].strip()
        proof = block[proof_match.end():].strip()
    label_match = LABEL_PATTERN.search(title)
    label = label_match.group(1) if label_match else ""
    return {
        "title": title,
        "statement": statement,
        "proof": proof,
        "label": label,
    }


def build_current_blueprint_verification_map(blueprint_path: Path, theorem_library_path: Path) -> Dict[str, Dict[str, Any]]:
    if not blueprint_path.exists():
        return {}
    blocks = [extract_blueprint_block(block) for block in split_top_level_blocks(blueprint_path.read_text(encoding="utf-8"))]
    theorem_library = read_json(theorem_library_path) if theorem_library_path.exists() else {}
    accepted_statement_keys = {
        key
        for key, value in ((theorem_library or {}).get("accepted") or {}).items()
        if isinstance(value, dict) and value.get("accepted") is True
    }
    labels_by_index: Dict[int, str] = {}
    indices_by_label: Dict[str, int] = {}
    for idx, block in enumerate(blocks, start=1):
        label = block.get("label") or ""
        if label:
            labels_by_index[idx] = label
            indices_by_label[label] = idx

    dependencies: Dict[int, List[int]] = {}
    for idx, block in enumerate(blocks, start=1):
        refs = sorted(set(LABEL_PATTERN.findall(block.get("proof", ""))))
        dependencies[idx] = sorted(
            {
                indices_by_label[ref]
                for ref in refs
                if ref in indices_by_label and indices_by_label[ref] != idx
            }
        )

    def statement_key(statement: str) -> str:
        return hashlib.sha256(statement.encode("utf-8")).hexdigest()

    def closure(idx: int) -> List[int]:
        seen: set[int] = set()

        def dfs(current: int) -> None:
            for dep in dependencies.get(current, []):
                if dep in seen:
                    continue
                seen.add(dep)
                dfs(dep)

        dfs(idx)
        return sorted(seen)

    current: Dict[str, Dict[str, Any]] = {}
    for idx, block in enumerate(blocks, start=1):
        if statement_key(block.get("statement", "")) in accepted_statement_keys:
            continue
        dependency_cards = []
        for dep_idx in closure(idx):
            dep_block = blocks[dep_idx - 1]
            dependency_cards.append(
                (
                    dep_block.get("label") or dep_block.get("title") or "",
                    "\n".join(
                        [
                            dep_block.get("title", ""),
                            "",
                            "## statement",
                            dep_block.get("statement", "").strip(),
                        ]
                    ).strip(),
                )
            )
        dependency_cards.sort(key=lambda item: item[0])
        dependency_context = "\n\n".join(card for _, card in dependency_cards).strip()
        verification_key = hashlib.sha256(
            (
                block.get("statement", "")
                + "\n\n---PROOF---\n\n"
                + dependency_context
                + "\n\n"
                + block.get("proof", "")
            ).encode("utf-8")
        ).hexdigest()
        current[verification_key] = {
            "index": idx,
            "title": block.get("title", ""),
            "statement": block.get("statement", ""),
            "proof": block.get("proof", ""),
            "verification_key": verification_key,
        }
    return current


def latest_matching_wrong_verifier_result(blueprint_path: Path, theorem_library_path: Path) -> Optional[Dict[str, Any]]:
    current_map = build_current_blueprint_verification_map(blueprint_path, theorem_library_path)
    if not current_map or not VERIFIER_RESULTS_ROOT.exists():
        return None
    seen_completed_keys: set[str] = set()
    for run_dir in sorted(
        [p for p in VERIFIER_RESULTS_ROOT.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        state_path = run_dir / "state.json"
        verification_path = run_dir / "verification.json"
        if not state_path.exists() or not verification_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            verification_payload = json.loads(verification_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        verification_key = str(state.get("verification_key") or "")
        if verification_key not in current_map:
            continue
        if state.get("status") != "succeeded":
            continue
        if verification_key in seen_completed_keys:
            continue
        seen_completed_keys.add(verification_key)
        if verification_payload.get("verdict") != "wrong":
            continue
        verification_report = verification_payload.get("verification_report") or {}
        if not ((verification_report.get("critical_errors") or []) or (verification_report.get("gaps") or [])):
            continue
        matched = dict(current_map[verification_key])
        matched.update(
            {
                "run_id": run_dir.name,
                "verification": verification_payload,
            }
        )
        return matched
    return None


def latest_current_wrong_section_report(section_report_path: Path) -> Optional[Dict[str, Any]]:
    payload = read_json(section_report_path) if section_report_path.exists() else {}
    if not payload:
        return None
    pass_reports = payload.get("pass_reports") or []
    latest = pass_reports[-1] if isinstance(pass_reports, list) and pass_reports else {}
    if not isinstance(latest, dict):
        return None
    section_reports = latest.get("section_reports") or []
    failing_candidates = [
        r for r in section_reports
        if isinstance(r, dict) and r.get("verdict") not in {None, "correct"}
    ]
    if not failing_candidates:
        return None

    def failing_priority(report: Dict[str, Any]) -> tuple[int, int]:
        verification = (report.get("verification") or {}).get("verification_report") or {}
        has_math_issue = bool((verification.get("critical_errors") or []) or (verification.get("gaps") or []))
        idx = int(report.get("index") or 0)
        return (0 if has_math_issue else 1, -idx)

    chosen = sorted(failing_candidates, key=failing_priority)[0]
    verification = chosen.get("verification") or {}
    verification_report = verification.get("verification_report") or {}
    if not ((verification_report.get("critical_errors") or []) or (verification_report.get("gaps") or [])):
        return None
    return chosen


def build_prompt(
    args: argparse.Namespace,
    problem_id: str,
    blueprint_path: Path,
    section_report_path: Path,
    repair_brief_path: Path,
    suspect_claims_path: Path,
    theorem_library_path: Path,
    theorem_library_lint_path: Path,
    base_extra_prompt: str,
) -> str:
    def summarize_theorem_library_for_prompt(path: Path) -> str:
        payload = read_json(path) if path.exists() else {}
        accepted = payload.get("accepted") or {}
        if not isinstance(accepted, dict):
            return ""
        lines: List[str] = []
        for entry in accepted.values():
            if not isinstance(entry, dict) or entry.get("accepted") is not True:
                continue
            title = str(entry.get("title") or "")
            statement = str(entry.get("statement") or "").strip()
            if not title or not statement:
                continue
            lines.append(f"{title}\n{statement}\n")
        return "\n".join(lines).strip()

    def summarize_lint_for_prompt(path: Path) -> str:
        payload = read_json(path) if path.exists() else {}
        if not payload:
            return ""
        dep_issues = payload.get("accepted_dependency_issues") or []
        if dep_issues:
            return json.dumps({"accepted_dependency_issues": dep_issues}, ensure_ascii=False, indent=2)
        return ""

    theorem_library_payload = read_json(theorem_library_path) if theorem_library_path.exists() else {}
    accepted_titles = {
        str(entry.get("title", ""))
        for entry in (theorem_library_payload.get("accepted") or {}).values()
        if isinstance(entry, dict) and entry.get("accepted") is True
    }
    section_snapshot = latest_section_snapshot(section_report_path)
    current_states_by_title = section_snapshot.get("states_by_title") or {}
    current_wrong_report = latest_current_wrong_section_report(section_report_path)
    if current_wrong_report is None:
        current_wrong_report = latest_matching_wrong_verifier_result(blueprint_path, theorem_library_path)
    include_repair_brief = True
    if repair_brief_path.exists():
        try:
            repair_brief_payload = read_json(repair_brief_path)
        except Exception:  # noqa: BLE001
            repair_brief_payload = {}
        failing_locations = [str(x) for x in (repair_brief_payload.get("failing_locations") or [])]
        if failing_locations and all(location in accepted_titles for location in failing_locations):
            include_repair_brief = False
        if include_repair_brief and failing_locations:
            currently_wrong = any(
                isinstance(current_states_by_title.get(location), dict)
                and str(current_states_by_title.get(location, {}).get("scheduler_status") or "") == "wrong"
                for location in failing_locations
            )
            if not currently_wrong:
                include_repair_brief = False
    if current_wrong_report is not None:
        include_repair_brief = False

    prompt = (
        f"Use AGENTS.md exactly to solve the math problem in {args.problem_file}. "
        f"If memory/{problem_id}/ or results/{problem_id}/ already contain artifacts from an earlier run, "
        "resume from them instead of restarting from scratch. Preserve and build on prior memory, failed paths, and proof drafts."
    )
    if current_wrong_report is not None:
        verification = current_wrong_report.get("verification") or {}
        verifier_payload = {
            "title": current_wrong_report.get("title"),
            "run_id": current_wrong_report.get("run_id"),
            "verification_key": current_wrong_report.get("verification_key"),
            "verdict": current_wrong_report.get("verdict"),
            "verification_report": verification.get("verification_report") or {},
            "repair_hints": verification.get("repair_hints") or "",
        }
        prompt += (
            "\n\nCurrent verifier result for the active failing theorem:\n"
            + json.dumps(verifier_payload, ensure_ascii=False, indent=2)
            + "\nUse this verifier result directly as the repair target. Treat it as the primary source of truth for the current failing theorem."
        )
    if repair_brief_path.exists() and include_repair_brief:
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
    theorem_library_summary = summarize_theorem_library_for_prompt(theorem_library_path)
    if theorem_library_summary:
        prompt += (
            "\n\nAccepted theorem library:\n"
            + theorem_library_summary
            + "\nTreat these accepted theorem statements as established for this problem. "
            "Do not discard these results when you reorganize the proof; reuse them as proved lemmas."
        )
    lint_summary = summarize_lint_for_prompt(theorem_library_lint_path)
    if lint_summary:
        prompt += (
        "\n\nHard theorem-library lint report:\n"
            + lint_summary
            + "\nRepair any accepted-theorem dependency-closure issue conservatively without discarding genuinely accepted theorems."
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
            failing_candidates = [
                r for r in section_reports
                if isinstance(r, dict) and r.get("verdict") not in {None, "correct"}
            ]

            def failing_priority(report: Dict[str, Any]) -> tuple[int, int]:
                verification = (report.get("verification") or {}).get("verification_report") or {}
                has_math_issue = bool((verification.get("critical_errors") or []) or (verification.get("gaps") or []))
                idx = int(report.get("index") or 0)
                return (0 if has_math_issue else 1, -idx)

            failing = sorted(failing_candidates, key=failing_priority)[0] if failing_candidates else None
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
                    if report.get("critical_errors") or report.get("gaps"):
                        brief["summary"] = report.get("summary") or failing.get("error") or "Section verification failed."
                        brief["critical_errors"] = report.get("critical_errors") or []
                        brief["gaps"] = report.get("gaps") or []
                        brief["repair_hints"] = (failing.get("verification") or {}).get("repair_hints") or failing.get("error", "")
                        brief["next_actions"] = ["Repair the failing block", "Rerun section verification from pass 1"]
                    else:
                        brief["scope"] = "infrastructure"
                        brief["summary"] = failing.get("error") or "Section verification infrastructure failure."
                        brief["repair_hints"] = failing.get("error", "")
                        brief["next_actions"] = ["Rerun section verification", "Check verifier backend health"]
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


def run_theorem_library_lint(blueprint: Path, theorem_library_path: Path, output_path: Path) -> Dict[str, Any]:
    if not blueprint.exists() or not theorem_library_path.exists():
        return {}
    completed = subprocess.run(
        [
            sys.executable,
            str(THEOREM_LIBRARY_LINT),
            str(blueprint),
            "--theorem-library",
            str(theorem_library_path),
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "error": completed.stderr or completed.stdout or f"lint exited {completed.returncode}",
        }
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def pause_for_rate_limit(state: Dict[str, Any], state_path: Path, heartbeat_path: Path) -> int:
    state["status"] = "paused_rate_limit"
    state["updated_at_utc"] = utc_now()
    write_json(state_path, state)
    while True:
        budget = maybe_restore_default_limit()
        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        state["updated_at_utc"] = utc_now()
        state["codex_budget"] = budget
        write_json(state_path, state)
        if str(budget.get("cooldown_until_utc") or "") == "":
            state["status"] = "retrying"
            state["updated_at_utc"] = utc_now()
            write_json(state_path, state)
            return 0
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
                if timeout_seconds > 0 and time.monotonic() - start >= timeout_seconds:
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
                    if args.attempt_timeout_seconds > 0 and time.monotonic() - start >= args.attempt_timeout_seconds:
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


def run_section_verification_phase(
    args: argparse.Namespace,
    blueprint: Path,
    section_blueprint: Path,
    section_report: Path,
    results_dir: Path,
    heartbeat_path: Path,
    state_path: Path,
    state: Dict[str, Any],
) -> int:
    shutil.copy2(blueprint, section_blueprint)
    section_verify_log = results_dir / "section_verify_run.log"
    state["current_phase"] = "section_verifying"
    state["section_blueprint"] = str(section_blueprint)
    write_json(state_path, state)
    return run_monitored_subprocess(
        [
            sys.executable,
            str(SECTION_VERIFY),
            "--resume-existing",
            "--mode",
            args.section_verify_mode,
            "--max-workers",
            str(args.section_verify_max_workers),
            "--passes-required",
            "3",
            "--output",
            str(section_report),
            str(section_blueprint),
        ],
        cwd=REPO_ROOT,
        log_path=section_verify_log,
        timeout_seconds=args.section_verify_timeout_seconds,
        heartbeat_path=heartbeat_path,
        state_path=state_path,
        state=state,
        phase="section_verifying",
    )


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
    manual_pause_path = results_dir / "manual_pause_recovery"
    heartbeat_path = results_dir / "heartbeat.txt"
    verified_blueprint = results_dir / "blueprint_verified.md"
    blueprint = results_dir / "blueprint.md"
    section_blueprint = results_dir / "section_blueprint.md"
    section_report = results_dir / "section_verification.json"
    stale_section_report = results_dir / "section_verification.stale_preclean.json"
    repair_brief_path = results_dir / "repair_brief.json"
    suspect_claims_path = results_dir / "suspect_claims.json"
    theorem_library_path = results_dir / "theorem_library.json"
    theorem_library_lint_path = results_dir / "theorem_library_lint.json"
    verification_cache_path = results_dir / "verification_cache.json"
    loop_journal_path = results_dir / "loop_journal.jsonl"
    snapshots_dir = results_dir / "attempt_snapshots"
    memory_verification_path = MEMORY_ROOT / problem_id / "verification_reports.jsonl"

    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    if manual_pause_path.exists():
        print(f"Manual recovery pause requested via {manual_pause_path}")
        return 0
    acquire_lock(lock_path)

    state = read_json(state_path)
    state.setdefault("problem_id", problem_id)
    state.setdefault("problem_file", args.problem_file)
    state.setdefault("created_at_utc", utc_now())
    state.setdefault("attempts", [])
    state.setdefault("execution_id", execution_id_now())
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
        attempt_execution_id = f"{state['execution_id']}:attempt{attempt_num}"
        active_blueprint = active_section_blueprint_path(state, blueprint, section_blueprint)
        verifier_health = fetch_verifier_health(args.verify_url)
        if (
            state.get("status") == "blocked_on_verifier_backend"
            and not verifier_health.get("active_run_id")
            and str(verifier_health.get("queue_depth", "0")) == "0"
        ):
            state["status"] = "retrying"
            state["current_phase"] = "recovering_from_idle_verifier"
            state["updated_at_utc"] = utc_now()
            write_json(state_path, state)
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "recovered_from_idle_verifier",
                    "attempt": attempt_num,
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
                },
            )

        heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
        state["status"] = "running"
        state["active_attempt"] = attempt_num
        state["runner_pid"] = os.getpid()
        state["current_phase"] = "generation"
        state["attempt_execution_id"] = attempt_execution_id
        state["updated_at_utc"] = utc_now()
        write_json(state_path, state)
        append_jsonl(
            loop_journal_path,
            {
                "timestamp_utc": utc_now(),
                "event": "attempt_started",
                "attempt": attempt_num,
                "execution_id": state["execution_id"],
                "attempt_execution_id": attempt_execution_id,
            },
        )

        if not verifier_health or not verification_service_ok(args.verify_url):
            state["status"] = "blocked_verifier"
            state["updated_at_utc"] = utc_now()
            state["last_failure_type"] = "verifier_unreachable"
            write_json(state_path, state)
            time.sleep(args.backoff_seconds)
            continue

        if active_blueprint.exists():
            refresh_verification_cache_from_results(
                blueprint_path=active_blueprint,
                theorem_library_path=theorem_library_path,
                verifier_results_root=VERIFIER_RESULTS_ROOT,
                output_path=verification_cache_path,
            )

        scheduler_view = (
            build_current_scheduler_view(
                blueprint_path=active_blueprint,
                theorem_library_path=theorem_library_path,
                verification_cache_path=verification_cache_path,
            )
            if active_blueprint.exists()
            else {"blocks": []}
        )
        current_matching_wrong = (
            latest_current_wrong_from_scheduler_view(
                scheduler_view=scheduler_view,
                verification_cache_path=verification_cache_path,
            )
            if active_blueprint.exists()
            else None
        )

        if current_matching_wrong is not None:
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "current_wrong_result_detected",
                    "attempt": attempt_num,
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
                    "title": current_matching_wrong.get("title"),
                    "run_id": current_matching_wrong.get("run_id"),
                    "verification_key": current_matching_wrong.get("verification_key"),
                },
            )

        if (
            active_blueprint.exists()
            and current_matching_wrong is None
            and should_resume_section_verification_from_current_view(
                blueprint_path=active_blueprint,
                theorem_library_path=theorem_library_path,
                verification_cache_path=verification_cache_path,
            )
        ):
            state["status"] = "running"
            state["runner_pid"] = os.getpid()
            state["current_phase"] = "section_verifying"
            state["updated_at_utc"] = utc_now()
            write_json(state_path, state)
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "section_resume_started",
                    "attempt": attempt_num,
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
                },
            )
            try:
                run_section_verification_phase(
                    args=args,
                    blueprint=blueprint,
                    section_blueprint=section_blueprint,
                    section_report=section_report,
                    results_dir=results_dir,
                    heartbeat_path=heartbeat_path,
                    state_path=state_path,
                    state=state,
                )
            except Exception:  # noqa: BLE001
                pass
            theorem_library_lint = run_theorem_library_lint(active_blueprint, theorem_library_path, theorem_library_lint_path)
            if theorem_library_lint:
                append_jsonl(
                    loop_journal_path,
                    {
                        "timestamp_utc": utc_now(),
                        "event": "theorem_library_lint",
                        "attempt": attempt_num,
                        "execution_id": state["execution_id"],
                        "attempt_execution_id": attempt_execution_id,
                        "theorem_library_lint": theorem_library_lint,
                    },
                )
            repair_brief = build_repair_brief(section_report, memory_verification_path)
            if repair_brief:
                write_json(repair_brief_path, repair_brief)
            suspect_claims = update_suspect_claims(suspect_claims_path, repair_brief)
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "section_resume_finished",
                    "attempt": attempt_num,
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
                    "repair_brief_written": bool(repair_brief),
                    "suspect_claims_count": len(suspect_claims),
                },
            )
            if verified_blueprint.exists():
                state["status"] = "verified"
                state["verified_blueprint"] = str(verified_blueprint)
                state["updated_at_utc"] = utc_now()
                write_json(state_path, state)
                heartbeat_path.write_text(utc_now() + "\n", encoding="utf-8")
                print(f"Verified blueprint written to {verified_blueprint}")
                return 0

        log_file = logs_dir / f"{problem_id}-attempt{attempt_num:02d}.md"
        state["current_log"] = str(log_file)
        state["attempt_started_at_utc"] = utc_now()
        prompt = build_prompt(
            args,
            problem_id,
            active_section_blueprint_path(state, blueprint, section_blueprint),
            section_report,
            repair_brief_path,
            suspect_claims_path,
            theorem_library_path,
            theorem_library_lint_path,
            base_extra_prompt,
        )
        write_json(state_path, state)
        cleanup_orphan_generator_codex()
        exit_code = run_attempt(args, problem_id, attempt_num, log_file, prompt, heartbeat_path, state_path, state)
        log_text = log_file.read_text(encoding="utf-8", errors="ignore")
        failure_type = classify_failure(log_text) if exit_code != 0 else ""

        state["attempts"].append(
            {
                "attempt": attempt_num,
                "attempt_execution_id": attempt_execution_id,
                "started_log": str(log_file),
                "exit_code": exit_code,
                "failure_type": failure_type,
                "finished_at_utc": utc_now(),
            }
        )
        state["updated_at_utc"] = utc_now()

        if blueprint.exists():
            try:
                section_exit = run_section_verification_phase(
                    args=args,
                    blueprint=blueprint,
                    section_blueprint=section_blueprint,
                    section_report=section_report,
                    results_dir=results_dir,
                    heartbeat_path=heartbeat_path,
                    state_path=state_path,
                    state=state,
                )
                if section_exit != 0 and not section_report.exists():
                    failure_type = failure_type or "section_verifier_failed"
            except Exception:  # noqa: BLE001
                pass

        active_blueprint = active_section_blueprint_path(state, blueprint, section_blueprint)
        if active_blueprint.exists():
            refresh_verification_cache_from_results(
                blueprint_path=active_blueprint,
                theorem_library_path=theorem_library_path,
                verifier_results_root=VERIFIER_RESULTS_ROOT,
                output_path=verification_cache_path,
            )

        theorem_library_lint = run_theorem_library_lint(active_blueprint, theorem_library_path, theorem_library_lint_path)
        if theorem_library_lint:
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "theorem_library_lint",
                    "attempt": attempt_num,
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
                    "theorem_library_lint": theorem_library_lint,
                },
            )

        repair_brief = build_repair_brief(section_report, memory_verification_path)
        if repair_brief:
            write_json(repair_brief_path, repair_brief)
            append_jsonl(
                loop_journal_path,
                {
                    "timestamp_utc": utc_now(),
                    "event": "repair_brief_written",
                    "attempt": attempt_num,
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
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
                    "execution_id": state["execution_id"],
                    "attempt_execution_id": attempt_execution_id,
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
                "execution_id": state["execution_id"],
                "attempt_execution_id": attempt_execution_id,
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
