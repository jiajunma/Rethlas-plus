#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


VERIFY_URL = "http://127.0.0.1:8091"
DEFAULT_TIMEOUT_SECONDS = 3600
POLL_INTERVAL_SECONDS = 5
PROOF_LINE_TARGET = 100
LABEL_PATTERN = re.compile(r"(lem:[A-Za-z0-9_]+|prop:[A-Za-z0-9_]+|thm:[A-Za-z0-9_]+)")
THEOREM_LIBRARY_FILENAME = "theorem_library.json"
LEGACY_VERIFIED_CACHE_FILENAME = "section_verified_cache.json"


@dataclass
class ProofBlock:
    title: str
    markdown: str
    kind: str
    statement: str
    proof: str
    proof_nonblank_lines: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("blueprint", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--verify-url", default=VERIFY_URL)
    parser.add_argument("--mode", choices=("sequential", "parallel"), default="sequential")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--passes-required", type=int, default=3)
    parser.add_argument("--accept-after-verifies", type=int, default=3)
    parser.add_argument("--skip-theorem-library-writes", action="store_true")
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--resume-existing", action="store_true")
    return parser.parse_args()


def compute_input_hash(statement: str, proof_text: str) -> str:
    payload = statement + "\n\n---PROOF---\n\n" + proof_text
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_statement_key(statement: str) -> str:
    payload = statement
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_top_level_blocks(text: str) -> List[str]:
    lines = text.splitlines()
    starts: List[int] = []
    for i, line in enumerate(lines):
        if line.startswith("# "):
            starts.append(i)
    if not starts:
        return []
    starts.append(len(lines))
    blocks: List[str] = []
    for a, b in zip(starts, starts[1:]):
        block = "\n".join(lines[a:b]).strip() + "\n"
        blocks.append(block)
    return blocks


def extract_block_parts(block: str) -> ProofBlock:
    lines = block.splitlines()
    title = lines[0].strip()
    kind = title[2:].split(maxsplit=1)[0].lower() if title.startswith("# ") else "unknown"

    statement_match = re.search(r"^## statement\s*$", block, flags=re.MULTILINE)
    proof_match = re.search(r"^## proof\s*$", block, flags=re.MULTILINE)

    statement = ""
    proof = ""
    if statement_match and proof_match and statement_match.end() <= proof_match.start():
        statement = block[statement_match.end():proof_match.start()].strip()
        proof = block[proof_match.end():].strip()

    proof_nonblank_lines = sum(1 for line in proof.splitlines() if line.strip())

    return ProofBlock(
        title=title,
        markdown=block,
        kind=kind,
        statement=statement,
        proof=proof,
        proof_nonblank_lines=proof_nonblank_lines,
    )


def structure_report(blocks: List[ProofBlock]) -> Dict[str, Any]:
    issues: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    if not blocks:
        issues.append({"location": "blueprint", "issue": "No top-level proof blocks found."})
        return {
            "summary": "No top-level blocks.",
            "issues": issues,
            "warnings": warnings,
            "block_titles": [],
        }

    for block in blocks:
        if not block.statement:
            issues.append({"location": block.title, "issue": "Missing `## statement` section."})
        if not block.proof:
            issues.append({"location": block.title, "issue": "Missing `## proof` section."})
        if block.proof_nonblank_lines > PROOF_LINE_TARGET:
            warnings.append(
                {
                    "location": block.title,
                    "issue": f"Proof has {block.proof_nonblank_lines} non-blank lines; exceeds the {PROOF_LINE_TARGET}-line quality target.",
                }
            )

    if blocks[-1].kind != "theorem":
        issues.append(
            {
                "location": blocks[-1].title,
                "issue": "The final top-level block is not a theorem.",
            }
        )

    summary = "Structure check passed." if not issues else "Structure check found issues."
    return {
        "summary": summary,
        "issues": issues,
        "warnings": warnings,
        "block_titles": [block.title for block in blocks],
    }


def extract_label(title: str) -> Optional[str]:
    match = LABEL_PATTERN.search(title)
    return match.group(1) if match else None


def build_dependency_data(blocks: List[ProofBlock]) -> Dict[str, Any]:
    labels_by_index: Dict[int, str] = {}
    indices_by_label: Dict[str, int] = {}
    for idx, block in enumerate(blocks, start=1):
        label = extract_label(block.title)
        if label:
            labels_by_index[idx] = label
            indices_by_label[label] = idx

    dependencies: Dict[int, List[int]] = {}
    for idx, block in enumerate(blocks, start=1):
        refs = set(LABEL_PATTERN.findall(block.proof))
        deps: List[int] = []
        for ref in sorted(refs):
            dep_idx = indices_by_label.get(ref)
            if dep_idx is not None and dep_idx != idx:
                deps.append(dep_idx)
        dependencies[idx] = sorted(set(deps))

    return {
        "labels_by_index": labels_by_index,
        "indices_by_label": indices_by_label,
        "dependencies": dependencies,
    }


def call_verifier(
    verify_url: str,
    statement: str,
    proof: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    payload = {"statement": statement, "proof": proof}
    submit = requests.post(
        f"{verify_url}/verify_async",
        json=payload,
        timeout=30,
    )
    if submit.status_code == 404:
        sync_response = requests.post(
            f"{verify_url}/verify",
            json=payload,
            timeout=timeout_seconds,
        )
        sync_response.raise_for_status()
        sync_payload = sync_response.json()
        if not isinstance(sync_payload, dict):
            raise ValueError("Verifier response must be a JSON object.")
        return sync_payload

    submit.raise_for_status()
    accepted = submit.json()
    run_id = accepted.get("run_id")
    if not run_id:
        raise ValueError("Verifier did not return a run_id.")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = None
        try:
            status_resp = requests.get(f"{verify_url}/verify_status/{run_id}", timeout=30)
            if status_resp.ok:
                status_payload = status_resp.json()
                status = status_payload.get("status")
        except requests.RequestException:
            status = None
        if status == "succeeded":
            result_resp = requests.get(f"{verify_url}/verify_result/{run_id}", timeout=30)
            result_resp.raise_for_status()
            payload = result_resp.json()
            if not isinstance(payload, dict):
                raise ValueError("Verifier response must be a JSON object.")
            return payload
        # Some backend versions materialize the result before they flip the status
        # away from "running". Probe the result endpoint so section verification
        # can still make progress on those deployments.
        try:
            result_resp = requests.get(f"{verify_url}/verify_result/{run_id}", timeout=30)
            if result_resp.status_code == 200:
                payload = result_resp.json()
                if not isinstance(payload, dict):
                    raise ValueError("Verifier response must be a JSON object.")
                return payload
        except requests.RequestException:
            pass
        if status in {"failed", "timed_out", "interrupted"}:
            raise RuntimeError(f"Verifier run {run_id} ended with status={status}")
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"Verifier run {run_id} did not finish within {timeout_seconds} seconds")


def classify_report(report: Dict[str, Any]) -> str:
    verdict = report.get("verdict")
    if verdict == "correct":
        return "correct"
    if verdict == "infrastructure_error":
        return "infrastructure_error"
    verification = (report.get("verification") or {}).get("verification_report") or {}
    critical_errors = verification.get("critical_errors") or []
    gaps = verification.get("gaps") or []
    if critical_errors:
        return "critical"
    if gaps:
        return "gap"
    if report.get("error"):
        return "infrastructure_error"
    return "critical"


def dedupe_section_reports(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_index: Dict[int, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    priority = {
        "correct": 4,
        "gap": 3,
        "critical": 2,
        "infrastructure_error": 1,
    }
    for report in reports:
        if not isinstance(report, dict):
            continue
        idx = report.get("index")
        if not isinstance(idx, int):
            extras.append(report)
            continue
        existing = by_index.get(idx)
        if existing is None:
            by_index[idx] = report
            continue
        existing_kind = classify_report(existing)
        new_kind = classify_report(report)
        if priority.get(new_kind, 0) >= priority.get(existing_kind, 0):
            by_index[idx] = report
    return extras + [by_index[idx] for idx in sorted(by_index)]


def upsert_section_report(section_reports: List[Dict[str, Any]], report: Dict[str, Any]) -> None:
    idx = report.get("index")
    if not isinstance(idx, int):
        section_reports.append(report)
        return
    for pos, existing in enumerate(section_reports):
        if isinstance(existing, dict) and existing.get("index") == idx:
            section_reports[pos] = report
            return
    section_reports.append(report)


def dependency_closure(dependencies: Dict[int, List[int]], idx: int) -> List[int]:
    seen: set[int] = set()

    def dfs(current: int) -> None:
        for dep in dependencies.get(current, []):
            if dep in seen:
                continue
            seen.add(dep)
            dfs(dep)

    dfs(idx)
    return sorted(seen)


def unique_strings(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def main() -> int:
    args = parse_args()
    blueprint_path = args.blueprint.resolve()
    text = blueprint_path.read_text(encoding="utf-8")

    blocks = [extract_block_parts(block) for block in split_top_level_blocks(text)]
    structure = structure_report(blocks)
    dependency_data = build_dependency_data(blocks)

    results_dir = blueprint_path.parent
    output_path = args.output.resolve() if args.output is not None else results_dir / "section_verification.json"
    theorem_library_path = results_dir / THEOREM_LIBRARY_FILENAME
    legacy_verified_cache_path = results_dir / LEGACY_VERIFIED_CACHE_FILENAME
    block_statement_keys = {
        idx: compute_statement_key(block.statement)
        for idx, block in enumerate(blocks, start=1)
    }
    blocks_by_title = {block.title: (idx, block) for idx, block in enumerate(blocks, start=1)}
    dependencies: Dict[int, List[int]] = dependency_data["dependencies"]
    labels_by_index: Dict[int, str] = dependency_data["labels_by_index"]
    reverse_dependencies: Dict[int, List[int]] = {idx: [] for idx in range(1, len(blocks) + 1)}
    for idx, deps in dependencies.items():
        for dep in deps:
            reverse_dependencies.setdefault(dep, []).append(idx)
    dep_statement_keys_by_idx = {
        idx: [block_statement_keys[dep] for dep in dependencies.get(idx, [])]
        for idx in range(1, len(blocks) + 1)
    }

    def load_theorem_library() -> Dict[str, Dict[str, Any]]:
        for path in [theorem_library_path, legacy_verified_cache_path]:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            accepted = payload.get("accepted", {})
            if isinstance(accepted, dict):
                return accepted
        return {}

    def library_status(entry: Dict[str, Any]) -> str:
        status = entry.get("status")
        if isinstance(status, str) and status:
            return status
        if entry.get("invalidated"):
            return "invalidated"
        if entry.get("accepted"):
            return "accepted"
        if int(entry.get("correct_verify_count", 0) or 0) > 0:
            return "provisional"
        return "unverified"

    def is_reusable(entry: Optional[Dict[str, Any]]) -> bool:
        return isinstance(entry, dict) and library_status(entry) in {"accepted", "provisional"}

    def ensure_library_entry(idx: int, block: ProofBlock, cached_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        statement_key = block_statement_keys[idx]
        entry = theorem_library.setdefault(
            statement_key,
            {
                "title": block.title,
                "kind": block.kind,
                "label": labels_by_index.get(idx, ""),
                "statement": block.statement,
                "statement_key": statement_key,
                "dependency_labels": [labels_by_index.get(dep, "") for dep in dependencies.get(idx, []) if labels_by_index.get(dep, "")],
                "dependency_statement_keys": dep_statement_keys_by_idx[idx],
                "correct_observation_ids": [],
                "correct_verify_count": 0,
                "accepted": False,
                "status": "unverified",
                "accepted_at_utc": "",
                "last_verified_at_utc": "",
                "source_paths": [],
                "invalidated": False,
                "invalidated_at_utc": "",
                "invalidated_reason": "",
                "invalidated_by_statement_key": "",
                "report": cached_report or {},
            },
        )
        entry["title"] = block.title
        entry["kind"] = block.kind
        entry["label"] = labels_by_index.get(idx, "")
        entry["statement"] = block.statement
        entry["statement_key"] = statement_key
        entry["dependency_labels"] = unique_strings(
            [labels_by_index.get(dep, "") for dep in dependencies.get(idx, [])]
        )
        entry["dependency_statement_keys"] = dep_statement_keys_by_idx[idx]
        entry.setdefault("correct_observation_ids", [])
        entry["correct_verify_count"] = len(entry["correct_observation_ids"])
        entry.setdefault("accepted", False)
        entry.setdefault("status", "unverified")
        entry.setdefault("accepted_at_utc", "")
        entry.setdefault("last_verified_at_utc", "")
        entry.setdefault("source_paths", [])
        entry.setdefault("invalidated", False)
        entry.setdefault("invalidated_at_utc", "")
        entry.setdefault("invalidated_reason", "")
        entry.setdefault("invalidated_by_statement_key", "")
        if cached_report is not None:
            entry["report"] = cached_report
        return entry

    def recompute_library_statuses() -> None:
        current_statement_keys = set(block_statement_keys.values())
        for idx, block in enumerate(blocks, start=1):
            ensure_library_entry(idx, block)
        for key, entry in theorem_library.items():
            if not isinstance(entry, dict):
                continue
            if key not in current_statement_keys:
                if entry.get("invalidated"):
                    entry["status"] = "invalidated"
                    entry["accepted"] = False
                elif int(entry.get("correct_verify_count", 0) or 0) >= args.accept_after_verifies:
                    entry["status"] = "accepted"
                    entry["accepted"] = True
                elif int(entry.get("correct_verify_count", 0) or 0) > 0:
                    entry["status"] = "provisional"
                    entry["accepted"] = False
                else:
                    entry["status"] = "unverified"
                    entry["accepted"] = False
        for idx, block in enumerate(blocks, start=1):
            statement_key = block_statement_keys[idx]
            entry = theorem_library[statement_key]
            count = int(entry.get("correct_verify_count", 0) or 0)
            if entry.get("invalidated"):
                entry["status"] = "invalidated"
                entry["accepted"] = False
                continue
            if count <= 0:
                entry["status"] = "unverified"
                entry["accepted"] = False
                continue
            if count < args.accept_after_verifies:
                entry["status"] = "provisional"
                entry["accepted"] = False
                continue
            dep_keys = dep_statement_keys_by_idx[idx]
            if all(theorem_library.get(dep_key, {}).get("status") == "accepted" for dep_key in dep_keys):
                entry["status"] = "accepted"
                entry["accepted"] = True
                if not entry.get("accepted_at_utc"):
                    entry["accepted_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            else:
                entry["status"] = "provisional"
                entry["accepted"] = False

    def invalidate_entry_and_downstream(start_idx: int, reason: str) -> None:
        queue: List[int] = [start_idx]
        seen: set[int] = set()
        root_statement_key = block_statement_keys[start_idx]
        while queue:
            idx = queue.pop(0)
            if idx in seen:
                continue
            seen.add(idx)
            block = blocks[idx - 1]
            entry = ensure_library_entry(idx, block)
            entry["invalidated"] = True
            entry["invalidated_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            entry["invalidated_reason"] = reason
            entry["invalidated_by_statement_key"] = root_statement_key
            entry["accepted"] = False
            entry["status"] = "invalidated"
            for downstream_idx in reverse_dependencies.get(idx, []):
                queue.append(downstream_idx)
        recompute_library_statuses()
        write_theorem_library(theorem_library)

    def write_theorem_library(library: Dict[str, Dict[str, Any]]) -> None:
        payload = {
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "accepted": library,
        }
        theorem_library_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def bootstrap_theorem_library(library: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        changed = False
        source_paths = []
        if output_path.exists() and not args.skip_theorem_library_writes:
            source_paths.append(output_path)
        snapshots_dir = results_dir / "attempt_snapshots"
        if snapshots_dir.exists() and not args.skip_theorem_library_writes:
            source_paths.extend(sorted(snapshots_dir.glob("attempt*_section_verification.json")))
        for source_path in source_paths:
            try:
                payload = json.loads(source_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for pass_report in payload.get("pass_reports", []) or []:
                if not isinstance(pass_report, dict):
                    continue
                pass_index = pass_report.get("pass_index")
                for report in pass_report.get("section_reports", []) or []:
                    if not isinstance(report, dict) or classify_report(report) != "correct":
                        continue
                    title = report.get("title")
                    idx = report.get("index")
                    current: Optional[ProofBlock] = None
                    current_idx: Optional[int] = None
                    if isinstance(title, str) and title in blocks_by_title:
                        current_idx, current = blocks_by_title[title]
                    elif isinstance(idx, int) and 1 <= idx <= len(blocks):
                        block = blocks[idx - 1]
                        if block.title == title:
                            current_idx, current = idx, block
                    if current is None or current_idx is None:
                        continue
                    cached_report = dict(report)
                    cached_report["index"] = current_idx
                    cached_report["title"] = current.title
                    statement_key = block_statement_keys[current_idx]
                    cached_report["statement_key"] = statement_key
                    entry = ensure_library_entry(current_idx, current, cached_report)
                    observation_id = f"snapshot:{source_path}:pass{pass_index}:block{current_idx}"
                    if observation_id not in entry["correct_observation_ids"]:
                        entry["correct_observation_ids"].append(observation_id)
                    entry["correct_verify_count"] = len(entry["correct_observation_ids"])
                    entry["last_verified_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    if str(source_path) not in entry["source_paths"]:
                        entry["source_paths"].append(str(source_path))
                    entry["report"] = cached_report
                    changed = True
        recompute_library_statuses()
        if changed:
            write_theorem_library(library)
        return library

    theorem_library = bootstrap_theorem_library(load_theorem_library())
    verification_session_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    def record_verified_report(
        idx: int,
        block: ProofBlock,
        report: Dict[str, Any],
        source_path: Path,
        pass_index: int,
    ) -> None:
        if args.skip_theorem_library_writes:
            return
        cached_report = dict(report)
        cached_report["index"] = idx
        cached_report["title"] = block.title
        cached_report["statement_key"] = block_statement_keys[idx]
        entry = ensure_library_entry(idx, block, cached_report)
        observation_id = f"runtime:{verification_session_id}:pass{pass_index}:block{idx}"
        if observation_id not in entry["correct_observation_ids"]:
            entry["correct_observation_ids"].append(observation_id)
        entry["correct_verify_count"] = len(entry["correct_observation_ids"])
        entry["invalidated"] = False
        entry["invalidated_at_utc"] = ""
        entry["invalidated_reason"] = ""
        entry["invalidated_by_statement_key"] = ""
        entry["last_verified_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if str(source_path) not in entry["source_paths"]:
            entry["source_paths"].append(str(source_path))
        entry["report"] = cached_report
        recompute_library_statuses()
        write_theorem_library(theorem_library)

    def write_snapshot(
        pass_reports: List[Dict[str, Any]],
        overall_verdict: str,
        consecutive_passes: int,
    ) -> None:
        payload = {
            "blueprint_path": str(blueprint_path),
            "structure_report": structure,
            "dependency_data": dependency_data,
            "mode": args.mode,
            "max_workers": args.max_workers,
            "max_consecutive_failures": args.max_consecutive_failures,
            "passes_required": args.passes_required,
            "passes_completed": consecutive_passes,
            "pass_reports": pass_reports,
            "overall_verdict": overall_verdict,
        }
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def load_existing_section_reports(pass_index: int) -> List[Dict[str, Any]]:
        if not args.resume_existing or not output_path.exists():
            return []
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
        existing_passes = payload.get("pass_reports", [])
        if not isinstance(existing_passes, list):
            return []
        for item in existing_passes:
            if isinstance(item, dict) and item.get("pass_index") == pass_index:
                reports = item.get("section_reports", [])
                if isinstance(reports, list):
                    return dedupe_section_reports(reports)
        return []

    def run_one_pass(pass_index: int, completed_passes: int) -> Dict[str, Any]:
        def build_verification_context(idx: int) -> str:
            parts: List[str] = []
            for dep_idx in dependency_closure(dependencies, idx):
                dep_block = blocks[dep_idx - 1]
                dep_statement_key = block_statement_keys[dep_idx]
                cached = theorem_library.get(dep_statement_key)
                if is_reusable(cached):
                    status = library_status(cached)
                    note = (
                        "Accepted in theorem library after three successful verifier passes."
                        if status == "accepted"
                        else f"Provisionally verified in theorem library ({cached.get('correct_verify_count', 0)} successful verifier passes, not yet accepted)."
                    )
                    parts.append(
                        "\n".join(
                            [
                                dep_block.title,
                                "",
                                "## statement",
                                dep_block.statement.strip(),
                                "",
                                "## proof",
                                note,
                            ]
                        ).strip()
                        + "\n"
                    )
                else:
                    parts.append(dep_block.markdown.rstrip() + "\n")
            parts.append(blocks[idx - 1].markdown.rstrip() + "\n")
            return "\n".join(parts).strip() + "\n"

        verification_contexts = {
            idx: build_verification_context(idx)
            for idx in range(1, len(blocks) + 1)
        }
        expected_input_hashes = {
            idx: compute_input_hash(block.statement, verification_contexts[idx])
            for idx, block in enumerate(blocks, start=1)
        }

        def recompute_completed_indices(section_reports: List[Dict[str, Any]]) -> set[int]:
            completed: set[int] = set()
            for idx, block in enumerate(blocks, start=1):
                entry = theorem_library.get(block_statement_keys[idx])
                if is_reusable(entry):
                    completed.add(idx)
            for report in section_reports:
                if not isinstance(report, dict):
                    continue
                idx = report.get("index")
                if not isinstance(idx, int):
                    continue
                entry = theorem_library.get(block_statement_keys[idx])
                if entry is not None and library_status(entry) == "invalidated":
                    continue
                if classify_report(report) == "correct":
                    completed.add(idx)
            return completed

        section_reports: List[Dict[str, Any]] = []
        reusable_indices = set()
        for idx, block in enumerate(blocks, start=1):
            statement_key = block_statement_keys[idx]
            cached = theorem_library.get(statement_key)
            if not is_reusable(cached):
                continue
            report = cached.get("report")
            if not isinstance(report, dict):
                continue
            reused = dict(report)
            reused["index"] = idx
            reused["title"] = block.title
            reused["statement_key"] = statement_key
            reused["reused_from_theorem_library"] = True
            upsert_section_report(section_reports, reused)
            reusable_indices.add(idx)
        for report in load_existing_section_reports(pass_index):
            if not isinstance(report, dict):
                continue
            idx = report.get("index")
            if not isinstance(idx, int):
                continue
            if idx in reusable_indices:
                continue
            if report.get("input_hash") != expected_input_hashes.get(idx):
                continue
            upsert_section_report(section_reports, report)

        overall_verdict = "correct"
        completed_indices = recompute_completed_indices(section_reports)

        def write_in_progress() -> None:
            partial_reports = pass_reports + [
                {
                    "section_reports": section_reports,
                    "overall_verdict": overall_verdict,
                    "pass_index": pass_index,
                }
            ]
            write_snapshot(partial_reports, overall_verdict, completed_passes)

        if structure["issues"]:
            return {
                "section_reports": section_reports,
                "overall_verdict": "wrong",
            }

        def verify_one(idx: int, block: ProofBlock, proof_text: str, input_hash: str) -> Dict[str, Any]:
            report: Dict[str, Any] = {
                "index": idx,
                "title": block.title,
                "kind": block.kind,
                "proof_nonblank_lines": block.proof_nonblank_lines,
                "input_hash": input_hash,
            }
            verifier_payload = call_verifier(
                verify_url=args.verify_url,
                statement=block.statement,
                proof=proof_text,
                timeout_seconds=args.timeout_seconds,
            )
            report["verification"] = verifier_payload
            report["verdict"] = verifier_payload.get("verdict", "wrong")
            return report

        if args.mode == "sequential":
            consecutive_failures = 0
            while True:
                ready_indices = [
                    idx
                    for idx in range(args.start_index, len(blocks) + 1)
                    if idx not in completed_indices
                    and all(dep in completed_indices for dep in dependencies.get(idx, []))
                ]
                if not ready_indices:
                    remaining_indices = [
                        idx for idx in range(args.start_index, len(blocks) + 1) if idx not in completed_indices
                    ]
                    if remaining_indices:
                        overall_verdict = "blocked_by_dependency"
                    break
                idx = min(ready_indices)
                block = blocks[idx - 1]
                proof_text = verification_contexts[idx]
                input_hash = expected_input_hashes[idx]
                print(
                    f"[verify_sections] pass starting block {idx}/{len(blocks)}: {block.title}",
                    flush=True,
                )
                try:
                    report = verify_one(idx, block, proof_text, input_hash)
                except Exception as exc:  # noqa: BLE001
                    report = {
                        "index": idx,
                        "title": block.title,
                        "kind": block.kind,
                        "proof_nonblank_lines": block.proof_nonblank_lines,
                        "input_hash": input_hash,
                        "verdict": "infrastructure_error",
                        "error": str(exc),
                    }
                    overall_verdict = "infrastructure_error"
                    upsert_section_report(section_reports, report)
                    write_in_progress()
                    print(
                        f"[verify_sections] block {idx} infrastructure_error: {exc}",
                        flush=True,
                    )
                    break

                upsert_section_report(section_reports, report)
                write_in_progress()
                print(
                    f"[verify_sections] block {idx} verdict: {report['verdict']}",
                    flush=True,
                )
                severity = classify_report(report)
                if severity == "correct":
                    consecutive_failures = 0
                    record_verified_report(idx, block, report, output_path, pass_index)
                    completed_indices = recompute_completed_indices(section_reports)
                elif severity == "gap":
                    overall_verdict = "wrong"
                    invalidate_entry_and_downstream(idx, f"gap in pass {pass_index}: {report.get('title', block.title)}")
                    completed_indices = recompute_completed_indices(section_reports)
                else:
                    consecutive_failures += 1
                    overall_verdict = "infrastructure_error" if severity == "infrastructure_error" else "wrong"
                    if severity == "critical":
                        invalidate_entry_and_downstream(idx, f"critical error in pass {pass_index}: {report.get('title', block.title)}")
                        completed_indices = recompute_completed_indices(section_reports)
                    if consecutive_failures >= args.max_consecutive_failures:
                        overall_verdict = "return_to_generator"
                        break
        else:
            max_workers = max(1, args.max_workers)
            while True:
                ready_indices = [
                    idx
                    for idx in range(args.start_index, len(blocks) + 1)
                    if idx not in completed_indices
                    and all(dep in completed_indices for dep in dependencies.get(idx, []))
                ]
                if not ready_indices:
                    remaining_indices = [
                        idx for idx in range(args.start_index, len(blocks) + 1) if idx not in completed_indices
                    ]
                    if remaining_indices and overall_verdict == "correct":
                        overall_verdict = "blocked_by_dependency"
                    break

                batch_indices = ready_indices[:max_workers]
                futures = {}
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for idx in batch_indices:
                        block = blocks[idx - 1]
                        proof_text = verification_contexts[idx]
                        input_hash = expected_input_hashes[idx]
                        futures[executor.submit(verify_one, idx, block, proof_text, input_hash)] = (idx, block, input_hash)

                    parallel_reports: Dict[int, Dict[str, Any]] = {}
                    for future in as_completed(futures):
                        idx, block, input_hash = futures[future]
                        try:
                            parallel_reports[idx] = future.result()
                        except Exception as exc:  # noqa: BLE001
                            parallel_reports[idx] = {
                                "index": idx,
                                "title": block.title,
                                "kind": block.kind,
                                "proof_nonblank_lines": block.proof_nonblank_lines,
                                "input_hash": input_hash,
                                "verdict": "infrastructure_error",
                                "error": str(exc),
                            }
                            if overall_verdict == "correct":
                                overall_verdict = "infrastructure_error"

                for idx in sorted(parallel_reports):
                    report = parallel_reports[idx]
                    block = blocks[idx - 1]
                    upsert_section_report(section_reports, report)
                    severity = classify_report(report)
                    if severity == "correct":
                        record_verified_report(idx, block, report, output_path, pass_index)
                    elif severity == "gap":
                        invalidate_entry_and_downstream(idx, f"gap in pass {pass_index}: {report.get('title', block.title)}")
                    elif severity == "critical":
                        invalidate_entry_and_downstream(idx, f"critical error in pass {pass_index}: {report.get('title', block.title)}")
                    completed_indices = recompute_completed_indices(section_reports)
                    if severity == "gap":
                        if overall_verdict == "correct":
                            overall_verdict = "wrong"
                    elif severity == "critical":
                        if overall_verdict == "correct":
                            overall_verdict = "wrong"
                    elif severity == "infrastructure_error":
                        overall_verdict = "infrastructure_error"
                if overall_verdict in {"infrastructure_error", "return_to_generator"}:
                    break

        return {
            "section_reports": section_reports,
            "overall_verdict": overall_verdict,
        }

    pass_reports: List[Dict[str, Any]] = []
    overall_verdict = "wrong"
    consecutive_passes = 0

    for pass_index in range(1, args.passes_required + 1):
        pass_result = run_one_pass(pass_index, consecutive_passes)
        pass_result["pass_index"] = pass_index
        pass_reports.append(pass_result)

        if pass_result["overall_verdict"] == "correct":
            consecutive_passes += 1
            write_snapshot(pass_reports, "correct", consecutive_passes)
        else:
            overall_verdict = pass_result["overall_verdict"]
            write_snapshot(pass_reports, overall_verdict, consecutive_passes)
            break

    if consecutive_passes == args.passes_required:
        overall_verdict = "correct"
    write_snapshot(pass_reports, overall_verdict, consecutive_passes)

    return 0 if overall_verdict == "correct" else 1


if __name__ == "__main__":
    raise SystemExit(main())
