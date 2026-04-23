#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theorem-library", type=Path, required=True)
    parser.add_argument("--verifier-results", type=Path, required=True)
    parser.add_argument("--target-label", default="")
    parser.add_argument("--target-title", default="")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_verifier_index(results_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    index: Dict[str, List[Dict[str, Any]]] = {}
    if not results_root.exists():
        return index
    for run_dir in sorted([p for p in results_root.iterdir() if p.is_dir()]):
        state_path = run_dir / "state.json"
        if not state_path.exists():
            continue
        try:
            state = load_json(state_path)
        except Exception:  # noqa: BLE001
            continue
        verification_key = str(state.get("verification_key") or "")
        if not verification_key:
            continue
        entry = {
            "run_id": run_dir.name,
            "status": str(state.get("status") or ""),
            "verdict": str(state.get("verdict") or ""),
            "updated_at_utc": str(state.get("updated_at_utc") or ""),
        }
        index.setdefault(verification_key, []).append(entry)
    return index


def find_targets(accepted: Dict[str, Dict[str, Any]], target_label: str, target_title: str) -> Set[str]:
    if not target_label and not target_title:
        return set(accepted.keys())
    targets: Set[str] = set()
    for statement_key, entry in accepted.items():
        if not isinstance(entry, dict):
            continue
        if target_label and str(entry.get("label") or "") == target_label:
            targets.add(statement_key)
        if target_title and str(entry.get("title") or "") == target_title:
            targets.add(statement_key)
    return targets


def dependency_closure(accepted: Dict[str, Dict[str, Any]], seeds: Set[str]) -> Set[str]:
    seen: Set[str] = set()
    stack = list(seeds)
    while stack:
        statement_key = stack.pop()
        if statement_key in seen:
            continue
        seen.add(statement_key)
        entry = accepted.get(statement_key) or {}
        for dep_key in entry.get("dependency_statement_keys") or []:
            if isinstance(dep_key, str) and dep_key in accepted:
                stack.append(dep_key)
    return seen


def validate_entry(
    statement_key: str,
    entry: Dict[str, Any],
    accepted: Dict[str, Dict[str, Any]],
    verifier_index: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    if not entry.get("accepted"):
        errors.append("entry is not marked accepted")
    if not str(entry.get("statement") or "").strip():
        errors.append("missing statement")
    if not str(entry.get("proof_markdown") or "").strip():
        errors.append("missing proof_markdown")
    if not str(entry.get("block_markdown") or "").strip():
        warnings.append("missing block_markdown")
    if not str(entry.get("proof_source_path") or "").strip():
        warnings.append("missing proof_source_path")
    if not str(entry.get("accepted_verification_key") or "").strip():
        errors.append("missing accepted_verification_key")

    dep_keys = entry.get("dependency_statement_keys") or []
    missing_deps = [dep_key for dep_key in dep_keys if dep_key not in accepted]
    if missing_deps:
        errors.append(f"missing accepted dependencies: {missing_deps}")

    accepted_verification_key = str(entry.get("accepted_verification_key") or "")
    matching_runs = verifier_index.get(accepted_verification_key, [])
    correct_runs = [
        run for run in matching_runs
        if run.get("status") == "succeeded" and run.get("verdict") == "correct"
    ]
    if accepted_verification_key and not correct_runs:
        errors.append("accepted_verification_key has no succeeded+correct verifier run")

    return {
        "statement_key": statement_key,
        "title": str(entry.get("title") or ""),
        "label": str(entry.get("label") or ""),
        "accepted_verification_key": accepted_verification_key,
        "correct_run_count_for_accepted_key": len(correct_runs),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    args = parse_args()
    theorem_library = load_json(args.theorem_library)
    accepted = theorem_library.get("accepted") or {}
    if not isinstance(accepted, dict):
        raise SystemExit("theorem library does not contain an 'accepted' object")

    verifier_index = build_verifier_index(args.verifier_results)
    seeds = find_targets(accepted, args.target_label, args.target_title)
    if not seeds:
        raise SystemExit("no theorem matched the requested target")
    closure = dependency_closure(accepted, seeds)

    entries = [
        validate_entry(statement_key, accepted[statement_key], accepted, verifier_index)
        for statement_key in sorted(closure)
    ]
    error_count = sum(len(item["errors"]) for item in entries)
    warning_count = sum(len(item["warnings"]) for item in entries)
    report = {
        "theorem_library_path": str(args.theorem_library.resolve()),
        "verifier_results_path": str(args.verifier_results.resolve()),
        "target_label": args.target_label,
        "target_title": args.target_title,
        "validated_statement_keys": sorted(closure),
        "entry_count": len(entries),
        "error_count": error_count,
        "warning_count": warning_count,
        "entries": entries,
        "complete": error_count == 0,
    }

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
