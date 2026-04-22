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

from verification_aggregation import refresh_verification_cache_from_results


VERIFY_URL = "http://127.0.0.1:8091"
DEFAULT_TIMEOUT_SECONDS = 3600
POLL_INTERVAL_SECONDS = 5
PROOF_LINE_TARGET = 200
LABEL_PATTERN = re.compile(r"(lem:[A-Za-z0-9_]+|prop:[A-Za-z0-9_]+|thm:[A-Za-z0-9_]+)")
THEOREM_LIBRARY_FILENAME = "theorem_library.json"
LEGACY_VERIFIED_CACHE_FILENAME = "section_verified_cache.json"
VERIFICATION_CACHE_FILENAME = "verification_cache.json"


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


def compute_verification_key(statement: str, dependency_context: str, proof: str) -> str:
    return compute_input_hash(statement, dependency_context + "\n\n" + proof)


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
    context: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    payload = {"statement": statement, "proof": proof, "context": context}
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
        sync_payload["run_id"] = sync_payload.get("run_id") or "sync"
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
            payload["run_id"] = run_id
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
                payload["run_id"] = run_id
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
    verification_cache_path = results_dir / VERIFICATION_CACHE_FILENAME
    legacy_verified_cache_path = results_dir / LEGACY_VERIFIED_CACHE_FILENAME
    verifier_results_root = Path(__file__).resolve().parents[2] / "verification" / "results"
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
    direct_dep_statement_keys_by_idx = {
        idx: [block_statement_keys[dep] for dep in dependencies.get(idx, [])]
        for idx in range(1, len(blocks) + 1)
    }
    closure_indices_by_idx = {
        idx: dependency_closure(dependencies, idx)
        for idx in range(1, len(blocks) + 1)
    }
    closure_statement_keys_by_idx = {
        idx: [block_statement_keys[dep] for dep in closure_indices_by_idx[idx]]
        for idx in range(1, len(blocks) + 1)
    }

    def load_theorem_library() -> Dict[str, Dict[str, Any]]:
        def sanitize_theorem_entry(statement_key: str, entry: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "title": str(entry.get("title") or ""),
                "kind": str(entry.get("kind") or ""),
                "label": str(entry.get("label") or ""),
                "statement": str(entry.get("statement") or ""),
                "statement_key": statement_key,
                "dependency_labels": unique_strings([str(x) for x in (entry.get("dependency_labels") or [])]),
                "dependency_statement_keys": unique_strings([str(x) for x in (entry.get("dependency_statement_keys") or [])]),
                "dependency_closure_statement_keys": unique_strings([str(x) for x in (entry.get("dependency_closure_statement_keys") or [])]),
                "accepted": True,
                "accepted_at_utc": str(entry.get("accepted_at_utc") or ""),
                "accepted_verification_key": str(entry.get("accepted_verification_key") or ""),
                "correct_verify_count": int(entry.get("correct_verify_count", 0) or 0),
                "source_paths": unique_strings([str(x) for x in (entry.get("source_paths") or [])]),
                "report": dict(entry.get("report") or {}) if isinstance(entry.get("report"), dict) else {},
            }

        for path in [theorem_library_path, legacy_verified_cache_path]:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            entries = payload.get("accepted", {})
            if not isinstance(entries, dict):
                continue
            accepted_only = {
                key: sanitize_theorem_entry(key, value)
                for key, value in entries.items()
                if isinstance(value, dict) and value.get("accepted") is True
            }
            if accepted_only:
                return accepted_only
        return {}

    def write_theorem_library(library: Dict[str, Dict[str, Any]]) -> None:
        accepted_only = {
            key: value
            for key, value in library.items()
            if isinstance(value, dict) and value.get("accepted") is True
        }
        payload = {
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "accepted": accepted_only,
        }
        theorem_library_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    theorem_library: Dict[str, Dict[str, Any]] = load_theorem_library()

    def load_verification_cache() -> Dict[str, Dict[str, Any]]:
        if verification_cache_path.exists():
            try:
                payload = json.loads(verification_cache_path.read_text(encoding="utf-8"))
                pairs = payload.get("pairs", {})
                if isinstance(pairs, dict):
                    return pairs
            except Exception:  # noqa: BLE001
                pass
        return {}

    def write_verification_cache(cache: Dict[str, Dict[str, Any]]) -> None:
        payload = {
            "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pairs": cache,
        }
        verification_cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    verification_cache: Dict[str, Dict[str, Any]] = load_verification_cache()

    def ensure_cache_entry(verification_key: str, idx: int, block: ProofBlock) -> Dict[str, Any]:
        entry = verification_cache.setdefault(
            verification_key,
            {
                "verification_key": verification_key,
                "statement_key": block_statement_keys[idx],
                "title": block.title,
                "kind": block.kind,
                "label": labels_by_index.get(idx, ""),
                "statement": block.statement,
                "dependency_statement_keys": closure_statement_keys_by_idx[idx],
                "correct_streak": 0,
                "total_correct_verifies": 0,
                "correct_run_ids": [],
                "last_result": "",
                "last_run_id": "",
                "last_verified_at_utc": "",
                "source_paths": [],
                "report": {},
            },
        )
        entry["statement_key"] = block_statement_keys[idx]
        entry["title"] = block.title
        entry["kind"] = block.kind
        entry["label"] = labels_by_index.get(idx, "")
        entry["statement"] = block.statement
        entry["dependency_statement_keys"] = closure_statement_keys_by_idx[idx]
        entry.setdefault("correct_streak", 0)
        entry.setdefault("total_correct_verifies", 0)
        entry.setdefault("correct_run_ids", [])
        entry.setdefault("last_result", "")
        entry.setdefault("last_run_id", "")
        entry.setdefault("last_verified_at_utc", "")
        entry.setdefault("source_paths", [])
        entry.setdefault("report", {})
        return entry

    def record_cache_observation(
        cache_entry: Dict[str, Any],
        classification: str,
        run_id: str,
        source_path: Path,
        verification_payload: Optional[Dict[str, Any]],
        observed_at_utc: str,
    ) -> None:
        if classification == "correct":
            is_new_run = run_id not in cache_entry["correct_run_ids"]
            if run_id not in cache_entry["correct_run_ids"]:
                cache_entry["correct_run_ids"].append(run_id)
                cache_entry["total_correct_verifies"] = int(cache_entry.get("total_correct_verifies", 0) or 0) + 1
            if is_new_run:
                previous = str(cache_entry.get("last_result") or "")
                cache_entry["correct_streak"] = cache_entry["correct_streak"] + 1 if previous == "correct" else 1
            if isinstance(verification_payload, dict):
                cache_entry["report"] = dict(verification_payload)
        elif classification in {"gap", "critical", "infrastructure_error"}:
            cache_entry["correct_streak"] = 0
            if isinstance(verification_payload, dict):
                cache_entry["report"] = dict(verification_payload)
        cache_entry["last_result"] = classification
        cache_entry["last_run_id"] = run_id
        cache_entry["last_verified_at_utc"] = observed_at_utc
        if str(source_path) not in cache_entry["source_paths"]:
            cache_entry["source_paths"].append(str(source_path))

    def bootstrap_verification_cache_from_snapshots(cache: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        source_paths: List[Path] = []
        stale_preclean_path = results_dir / "section_verification.stale_preclean.json"
        if stale_preclean_path.exists():
            source_paths.append(stale_preclean_path)
        snapshots_dir = results_dir / "attempt_snapshots"
        if snapshots_dir.exists():
            source_paths.extend(sorted(snapshots_dir.glob("attempt*_section_verification.json")))
        if output_path.exists():
            source_paths.append(output_path)

        ordered_events: List[tuple[int, int, Path, Dict[str, Any]]] = []
        for source_path in source_paths:
            try:
                payload = json.loads(source_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            attempt_match = re.search(r"attempt(\d+)", source_path.name)
            attempt_order = int(attempt_match.group(1)) if attempt_match else 10**9
            for pass_report in payload.get("pass_reports", []) or []:
                if not isinstance(pass_report, dict):
                    continue
                pass_index = int(pass_report.get("pass_index") or 0)
                for report in pass_report.get("section_reports", []) or []:
                    if isinstance(report, dict):
                        ordered_events.append((attempt_order, pass_index, source_path, report))

        ordered_events.sort(key=lambda item: (item[0], item[1], str(item[2])))
        changed = False
        for _attempt_order, _pass_index, source_path, report in ordered_events:
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
            verification_key = str(report.get("verification_key") or report.get("input_hash") or "")
            if not verification_key:
                continue
            classification = classify_report(report)
            if classification not in {"correct", "gap", "critical", "infrastructure_error"}:
                continue
            cache_entry = ensure_cache_entry(verification_key, current_idx, current)
            verification_payload = report.get("verification") if isinstance(report.get("verification"), dict) else None
            run_id = str(report.get("run_id") or f"snapshot:{source_path.name}:block{current_idx}")
            observed_at = str(report.get("observed_at_utc") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            record_cache_observation(
                cache_entry=cache_entry,
                classification=classification,
                run_id=run_id,
                source_path=source_path,
                verification_payload=verification_payload,
                observed_at_utc=observed_at,
            )
            changed = True
        if changed:
            write_verification_cache(cache)
        return cache

    verification_cache = bootstrap_verification_cache_from_snapshots(verification_cache)
    if not args.skip_theorem_library_writes:
        write_theorem_library(theorem_library)
    verification_cache = refresh_verification_cache_from_results(
        blueprint_path=blueprint_path,
        theorem_library_path=theorem_library_path,
        verifier_results_root=verifier_results_root,
        output_path=verification_cache_path,
    )

    verification_session_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    def current_pair_streak(verification_key: str) -> int:
        entry = verification_cache.get(verification_key)
        if not isinstance(entry, dict):
            return 0
        return int(entry.get("correct_streak", 0) or 0)

    def accepted_indices_set() -> set[int]:
        return {
            idx
            for idx, block in enumerate(blocks, start=1)
            if block_statement_keys[idx] in theorem_library
        }

    def update_cache_after_report(
        idx: int,
        block: ProofBlock,
        verification_key: str,
        report: Dict[str, Any],
        source_path: Path,
        pass_index: int,
    ) -> None:
        cache_entry = ensure_cache_entry(verification_key, idx, block)
        run_id = str(report.get("run_id") or f"{verification_session_id}:pass{pass_index}:block{idx}")
        classification = classify_report(report)
        record_cache_observation(
            cache_entry=cache_entry,
            classification=classification,
            run_id=run_id,
            source_path=source_path,
            verification_payload=report.get("verification") if isinstance(report.get("verification"), dict) else None,
            observed_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        write_verification_cache(verification_cache)

    def maybe_promote_to_accepted(idx: int, block: ProofBlock, verification_key: str, source_path: Path) -> None:
        if args.skip_theorem_library_writes:
            return
        if current_pair_streak(verification_key) < args.accept_after_verifies:
            return
        if not all(block_statement_keys[dep] in theorem_library for dep in dependencies.get(idx, [])):
            return
        cache_entry = ensure_cache_entry(verification_key, idx, block)
        theorem_library[block_statement_keys[idx]] = {
            "title": block.title,
            "kind": block.kind,
            "label": labels_by_index.get(idx, ""),
            "statement": block.statement,
            "statement_key": block_statement_keys[idx],
            "dependency_labels": unique_strings(
                [labels_by_index.get(dep, "") for dep in dependencies.get(idx, [])]
            ),
            "dependency_statement_keys": direct_dep_statement_keys_by_idx[idx],
            "dependency_closure_statement_keys": closure_statement_keys_by_idx[idx],
            "accepted": True,
            "accepted_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "accepted_verification_key": verification_key,
            "correct_verify_count": current_pair_streak(verification_key),
            "source_paths": unique_strings([str(source_path)] + [str(p) for p in cache_entry.get("source_paths", [])]),
            "report": dict(cache_entry.get("report") or {}),
        }
        write_theorem_library(theorem_library)

    def reconcile_promotions_from_cache(source_path: Path) -> None:
        changed = True
        while changed:
            changed = False
            for idx, block in enumerate(blocks, start=1):
                if block_statement_keys[idx] in theorem_library:
                    continue
                verification_key = verification_keys_by_idx[idx]
                if current_pair_streak(verification_key) < args.accept_after_verifies:
                    continue
                if not all(block_statement_keys[dep] in theorem_library for dep in dependencies.get(idx, [])):
                    continue
                maybe_promote_to_accepted(idx, block, verification_key, source_path)
                changed = True

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

    def recover_completed_verifier_reports(verification_keys_by_idx: Dict[int, str]) -> List[Dict[str, Any]]:
        if not verifier_results_root.exists():
            return []
        target_by_key = {
            verification_keys_by_idx[idx]: idx
            for idx in range(args.start_index, len(blocks) + 1)
        }
        recovered: Dict[int, Dict[str, Any]] = {}
        for run_dir in sorted(
            [p for p in verifier_results_root.iterdir() if p.is_dir()],
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
            if not isinstance(state, dict) or not isinstance(verification_payload, dict):
                continue
            if state.get("status") != "succeeded":
                continue
            verification_key = str(state.get("verification_key") or "")
            idx = target_by_key.get(verification_key)
            if idx is None or idx in recovered:
                continue
            block = blocks[idx - 1]
            recovered[idx] = {
                "index": idx,
                "title": block.title,
                "kind": block.kind,
                "proof_nonblank_lines": block.proof_nonblank_lines,
                "input_hash": verification_key,
                "verification_key": verification_key,
                "dependency_indices": dependencies.get(idx, []),
                "dependency_titles": [blocks[dep - 1].title for dep in dependencies.get(idx, [])],
                "observed_at_utc": str(state.get("updated_at_utc") or ""),
                "run_id": run_dir.name,
                "verification": verification_payload,
                "verdict": verification_payload.get("verdict", "wrong"),
                "recovered_from_verifier_results": True,
            }
        return [recovered[idx] for idx in sorted(recovered)]

    def build_dependency_context(idx: int) -> str:
        dependency_cards: List[tuple[str, str]] = []
        for dep_idx in closure_indices_by_idx[idx]:
            dep_block = blocks[dep_idx - 1]
            sort_key = labels_by_index.get(dep_idx, "") or dep_block.title
            card = "\n".join(
                [
                    dep_block.title,
                    "",
                    "## statement",
                    dep_block.statement.strip(),
                ]
            ).strip()
            dependency_cards.append((sort_key, card))
        dependency_cards.sort(key=lambda item: item[0])
        return ("\n\n".join(card for _, card in dependency_cards)).strip()

    dependency_contexts = {
        idx: build_dependency_context(idx)
        for idx in range(1, len(blocks) + 1)
    }
    verification_keys_by_idx = {
        idx: compute_verification_key(block.statement, dependency_contexts[idx], block.proof)
        for idx, block in enumerate(blocks, start=1)
    }
    reconcile_promotions_from_cache(output_path)

    def run_one_pass(pass_index: int, completed_passes: int) -> Dict[str, Any]:

        initial_accepted_indices = accepted_indices_set()
        deferred_indices: set[int] = set()

        def compute_failure_roots(section_reports: List[Dict[str, Any]]) -> set[int]:
            roots: set[int] = set()
            for report in section_reports:
                if not isinstance(report, dict):
                    continue
                idx = report.get("index")
                if not isinstance(idx, int):
                    continue
                if classify_report(report) in {"gap", "critical"}:
                    roots.add(idx)
            return roots

        def compute_invalidated_indices(failure_roots: set[int]) -> set[int]:
            invalidated: set[int] = set()
            queue = list(failure_roots)
            while queue:
                idx = queue.pop(0)
                if idx in invalidated:
                    continue
                invalidated.add(idx)
                for downstream_idx in reverse_dependencies.get(idx, []):
                    queue.append(downstream_idx)
            return invalidated

        def compute_session_sets(section_reports: List[Dict[str, Any]]) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
            accepted_indices = accepted_indices_set()
            failure_roots = compute_failure_roots(section_reports)
            invalidated_indices = compute_invalidated_indices(failure_roots)
            reports_by_index = {
                report.get("index"): report
                for report in section_reports
                if isinstance(report, dict) and isinstance(report.get("index"), int)
            }
            provisional_indices: set[int] = set()
            changed = True
            while changed:
                changed = False
                for idx, block in enumerate(blocks, start=1):
                    if idx in accepted_indices or idx in provisional_indices or idx in invalidated_indices:
                        continue
                    report = reports_by_index.get(idx)
                    if not isinstance(report, dict) or classify_report(report) != "correct":
                        continue
                    if all(dep in accepted_indices or dep in provisional_indices for dep in dependencies.get(idx, [])):
                        provisional_indices.add(idx)
                        changed = True
            completed_indices = accepted_indices | provisional_indices
            return accepted_indices, provisional_indices, failure_roots, invalidated_indices, completed_indices

        def build_block_states(
            section_reports: List[Dict[str, Any]],
            accepted_indices: set[int],
            provisional_indices: set[int],
            failure_roots: set[int],
            invalidated_indices: set[int],
            completed_indices: set[int],
        ) -> List[Dict[str, Any]]:
            reports_by_index = {
                report.get("index"): report
                for report in section_reports
                if isinstance(report, dict) and isinstance(report.get("index"), int)
            }
            states: List[Dict[str, Any]] = []
            for idx, block in enumerate(blocks, start=1):
                report = reports_by_index.get(idx) or {}
                unmet_deps = [dep for dep in dependencies.get(idx, []) if dep not in completed_indices]
                if idx in accepted_indices:
                    scheduler_status = "accepted"
                elif idx in provisional_indices:
                    scheduler_status = "provisional"
                elif idx in failure_roots:
                    scheduler_status = "wrong"
                elif idx in invalidated_indices:
                    scheduler_status = "invalidated"
                elif not unmet_deps:
                    scheduler_status = "ready"
                else:
                    scheduler_status = "blocked"
                states.append(
                    {
                        "index": idx,
                        "title": block.title,
                        "statement_key": block_statement_keys[idx],
                        "verification_key": verification_keys_by_idx[idx],
                        "scheduler_status": scheduler_status,
                        "last_report_classification": classify_report(report) if report else "",
                        "last_verdict": report.get("verdict") if isinstance(report, dict) else "",
                        "last_run_id": report.get("run_id") if isinstance(report, dict) else "",
                        "current_pair_streak": current_pair_streak(verification_keys_by_idx[idx]),
                        "passes_required_for_accept": args.accept_after_verifies,
                        "accepted_in_theorem_library": idx in accepted_indices,
                        "blocking_indices": unmet_deps,
                        "blocking_titles": [blocks[dep - 1].title for dep in unmet_deps],
                    }
                )
            return states

        def verification_candidates(
            accepted_indices: set[int],
            provisional_indices: set[int],
            failure_roots: set[int],
            invalidated_indices: set[int],
            completed_indices: set[int],
        ) -> List[int]:
            candidates = [
                idx
                for idx in range(args.start_index, len(blocks) + 1)
                if idx not in accepted_indices
                and idx not in provisional_indices
                and idx not in deferred_indices
                and idx not in failure_roots
                and idx not in invalidated_indices
                and all(dep in completed_indices for dep in dependencies.get(idx, []))
            ]
            candidates.sort(
                key=lambda idx: (
                    -current_pair_streak(verification_keys_by_idx[idx]),
                    idx,
                )
            )
            return candidates

        section_reports: List[Dict[str, Any]] = []
        for idx in initial_accepted_indices:
            block = blocks[idx - 1]
            cached = theorem_library.get(block_statement_keys[idx]) or {}
            report = cached.get("report")
            if not isinstance(report, dict):
                continue
            reused = dict(report)
            reused["index"] = idx
            reused["title"] = block.title
            reused["statement_key"] = block_statement_keys[idx]
            reused["reused_from_theorem_library"] = True
            upsert_section_report(section_reports, reused)
        for report in load_existing_section_reports(pass_index):
            if not isinstance(report, dict):
                continue
            idx = report.get("index")
            if not isinstance(idx, int):
                continue
            if idx in initial_accepted_indices:
                continue
            if report.get("verification_key") != verification_keys_by_idx.get(idx) and report.get("input_hash") != verification_keys_by_idx.get(idx):
                continue
            upsert_section_report(section_reports, report)
        for report in recover_completed_verifier_reports(verification_keys_by_idx):
            idx = report.get("index")
            if not isinstance(idx, int) or idx in initial_accepted_indices:
                continue
            upsert_section_report(section_reports, report)
            update_cache_after_report(idx, blocks[idx - 1], verification_keys_by_idx[idx], report, output_path, pass_index)
            reconcile_promotions_from_cache(output_path)
            maybe_promote_to_accepted(idx, blocks[idx - 1], verification_keys_by_idx[idx], output_path)

        overall_verdict = "correct"
        accepted_indices, provisional_indices, failure_roots, invalidated_indices, completed_indices = compute_session_sets(section_reports)

        def write_in_progress() -> None:
            accepted_now, provisional_now, failure_roots_now, invalidated_now, completed_now = compute_session_sets(section_reports)
            partial_reports = pass_reports + [
                {
                    "section_reports": section_reports,
                    "overall_verdict": overall_verdict,
                    "pass_index": pass_index,
                    "session_summary": {
                        "accepted": len(accepted_now),
                        "provisional": len(provisional_now),
                        "wrong_roots": len(failure_roots_now),
                        "invalidated": len(invalidated_now),
                        "ready": len(
                            [
                                idx
                                for idx in range(args.start_index, len(blocks) + 1)
                                if idx not in accepted_now
                                and idx not in provisional_now
                                and idx not in invalidated_now
                                and all(dep in completed_now for dep in dependencies.get(idx, []))
                            ]
                        ),
                        "blocked": len(
                            [
                                idx
                                for idx in range(args.start_index, len(blocks) + 1)
                                if idx not in accepted_now
                                and idx not in provisional_now
                                and idx not in invalidated_now
                                and not all(dep in completed_now for dep in dependencies.get(idx, []))
                            ]
                        ),
                    },
                    "block_states": build_block_states(
                        section_reports,
                        accepted_now,
                        provisional_now,
                        failure_roots_now,
                        invalidated_now,
                        completed_now,
                    ),
                }
            ]
            write_snapshot(partial_reports, overall_verdict, completed_passes)

        if structure["issues"]:
            accepted_now, provisional_now, failure_roots_now, invalidated_now, completed_now = compute_session_sets(section_reports)
            return {
                "section_reports": section_reports,
                "overall_verdict": "wrong",
                "session_summary": {
                    "accepted": len(accepted_now),
                    "provisional": len(provisional_now),
                    "wrong_roots": len(failure_roots_now),
                    "invalidated": len(invalidated_now),
                    "ready": 0,
                    "blocked": 0,
                },
                "block_states": build_block_states(
                    section_reports,
                    accepted_now,
                    provisional_now,
                    failure_roots_now,
                    invalidated_now,
                    completed_now,
                ),
            }

        def verify_one(idx: int, block: ProofBlock, dependency_context: str, input_hash: str) -> Dict[str, Any]:
            report: Dict[str, Any] = {
                "index": idx,
                "title": block.title,
                "kind": block.kind,
                "proof_nonblank_lines": block.proof_nonblank_lines,
                "input_hash": input_hash,
                "verification_key": input_hash,
                "dependency_indices": dependencies.get(idx, []),
                "dependency_titles": [blocks[dep - 1].title for dep in dependencies.get(idx, [])],
                "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            verifier_payload = call_verifier(
                verify_url=args.verify_url,
                statement=block.statement,
                proof=block.proof,
                context=dependency_context,
                timeout_seconds=args.timeout_seconds,
            )
            report["verification"] = verifier_payload
            report["verdict"] = verifier_payload.get("verdict", "wrong")
            return report

        if args.mode == "sequential":
            consecutive_failures = 0
            while True:
                candidate_indices = verification_candidates(
                    accepted_indices,
                    provisional_indices,
                    failure_roots,
                    invalidated_indices,
                    completed_indices,
                )
                if not candidate_indices:
                    remaining_indices = [
                        idx
                        for idx in range(args.start_index, len(blocks) + 1)
                        if idx not in accepted_indices
                        and idx not in provisional_indices
                        and idx not in failure_roots
                        and idx not in invalidated_indices
                    ]
                    if remaining_indices:
                        overall_verdict = (
                            "infrastructure_error"
                            if any(idx in deferred_indices for idx in remaining_indices)
                            else "blocked_by_dependency"
                        )
                    break
                idx = candidate_indices[0]
                block = blocks[idx - 1]
                dependency_context = dependency_contexts[idx]
                input_hash = verification_keys_by_idx[idx]
                print(
                    f"[verify_sections] pass starting block {idx}/{len(blocks)}: {block.title}",
                    flush=True,
                )
                try:
                    report = verify_one(idx, block, dependency_context, input_hash)
                except Exception as exc:  # noqa: BLE001
                    report = {
                        "index": idx,
                        "title": block.title,
                        "kind": block.kind,
                        "proof_nonblank_lines": block.proof_nonblank_lines,
                        "input_hash": input_hash,
                        "verification_key": input_hash,
                        "verdict": "infrastructure_error",
                        "error": str(exc),
                        "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
                    update_cache_after_report(idx, block, input_hash, report, output_path, pass_index)
                    reconcile_promotions_from_cache(output_path)
                    maybe_promote_to_accepted(idx, block, input_hash, output_path)
                    accepted_indices, provisional_indices, failure_roots, invalidated_indices, completed_indices = compute_session_sets(section_reports)
                elif severity == "gap":
                    overall_verdict = "wrong"
                    update_cache_after_report(idx, block, input_hash, report, output_path, pass_index)
                    reconcile_promotions_from_cache(output_path)
                    accepted_indices, provisional_indices, failure_roots, invalidated_indices, completed_indices = compute_session_sets(section_reports)
                    break
                else:
                    consecutive_failures += 1
                    if severity == "infrastructure_error":
                        deferred_indices.add(idx)
                    else:
                        overall_verdict = "wrong"
                    if severity == "critical":
                        update_cache_after_report(idx, block, input_hash, report, output_path, pass_index)
                        reconcile_promotions_from_cache(output_path)
                        accepted_indices, provisional_indices, failure_roots, invalidated_indices, completed_indices = compute_session_sets(section_reports)
                        break
                    if consecutive_failures >= args.max_consecutive_failures:
                        overall_verdict = "return_to_generator"
                        break
        else:
            max_workers = max(1, args.max_workers)
            while True:
                candidate_indices = verification_candidates(
                    accepted_indices,
                    provisional_indices,
                    failure_roots,
                    invalidated_indices,
                    completed_indices,
                )
                if not candidate_indices:
                    remaining_indices = [
                        idx
                        for idx in range(args.start_index, len(blocks) + 1)
                        if idx not in accepted_indices
                        and idx not in provisional_indices
                        and idx not in failure_roots
                        and idx not in invalidated_indices
                    ]
                    if remaining_indices and overall_verdict == "correct":
                        overall_verdict = (
                            "infrastructure_error"
                            if any(idx in deferred_indices for idx in remaining_indices)
                            else "blocked_by_dependency"
                        )
                    break

                batch_indices = candidate_indices[:max_workers]
                futures = {}
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for idx in batch_indices:
                        block = blocks[idx - 1]
                        dependency_context = dependency_contexts[idx]
                        input_hash = verification_keys_by_idx[idx]
                        futures[executor.submit(verify_one, idx, block, dependency_context, input_hash)] = (idx, block, input_hash)

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
                                "verification_key": input_hash,
                                "verdict": "infrastructure_error",
                                "error": str(exc),
                                "observed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            }
                            if overall_verdict == "correct":
                                overall_verdict = "infrastructure_error"

                for idx in sorted(parallel_reports):
                    report = parallel_reports[idx]
                    block = blocks[idx - 1]
                    upsert_section_report(section_reports, report)
                    severity = classify_report(report)
                    if severity == "correct":
                        update_cache_after_report(idx, block, verification_keys_by_idx[idx], report, output_path, pass_index)
                        reconcile_promotions_from_cache(output_path)
                        maybe_promote_to_accepted(idx, block, verification_keys_by_idx[idx], output_path)
                    elif severity == "gap":
                        update_cache_after_report(idx, block, verification_keys_by_idx[idx], report, output_path, pass_index)
                        reconcile_promotions_from_cache(output_path)
                    elif severity == "critical":
                        update_cache_after_report(idx, block, verification_keys_by_idx[idx], report, output_path, pass_index)
                        reconcile_promotions_from_cache(output_path)
                    accepted_indices, provisional_indices, failure_roots, invalidated_indices, completed_indices = compute_session_sets(section_reports)
                    if severity == "gap":
                        if overall_verdict == "correct":
                            overall_verdict = "wrong"
                    elif severity == "critical":
                        if overall_verdict == "correct":
                            overall_verdict = "wrong"
                    elif severity == "infrastructure_error":
                        deferred_indices.add(idx)
                if overall_verdict in {"wrong", "return_to_generator"}:
                    break

        accepted_now, provisional_now, failure_roots_now, invalidated_now, completed_now = compute_session_sets(section_reports)
        return {
            "section_reports": section_reports,
            "overall_verdict": overall_verdict,
            "session_summary": {
                "accepted": len(accepted_now),
                "provisional": len(provisional_now),
                "wrong_roots": len(failure_roots_now),
                "invalidated": len(invalidated_now),
                "ready": len(
                    [
                        idx
                        for idx in range(args.start_index, len(blocks) + 1)
                        if idx not in accepted_now
                        and idx not in provisional_now
                        and idx not in invalidated_now
                        and all(dep in completed_now for dep in dependencies.get(idx, []))
                    ]
                ),
                "blocked": len(
                    [
                        idx
                        for idx in range(args.start_index, len(blocks) + 1)
                        if idx not in accepted_now
                        and idx not in provisional_now
                        and idx not in invalidated_now
                        and not all(dep in completed_now for dep in dependencies.get(idx, []))
                    ]
                ),
            },
            "block_states": build_block_states(
                section_reports,
                accepted_now,
                provisional_now,
                failure_roots_now,
                invalidated_now,
                completed_now,
            ),
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
