#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


LABEL_PATTERN = re.compile(r"(lem:[A-Za-z0-9_]+|prop:[A-Za-z0-9_]+|thm:[A-Za-z0-9_]+)")


def compute_statement_key(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def compute_verification_key(statement: str, dependency_context: str, proof: str) -> str:
    payload = statement + "\n\n---CONTEXT---\n\n" + dependency_context + "\n\n---PROOF---\n\n" + proof
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
    return ["\n".join(lines[a:b]).strip() + "\n" for a, b in zip(starts, starts[1:])]


def extract_block(block: str) -> Dict[str, Any]:
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
        "label": label,
        "statement": statement,
        "proof": proof,
        "statement_key": compute_statement_key(statement),
    }


def load_accepted_statement_keys(theorem_library_path: Path) -> set[str]:
    if not theorem_library_path.exists():
        return set()
    try:
        payload = json.loads(theorem_library_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return set()
    accepted = payload.get("accepted") or {}
    if not isinstance(accepted, dict):
        return set()
    return {
        key
        for key, value in accepted.items()
        if isinstance(value, dict) and value.get("accepted") is True
    }


def build_current_verification_map(
    blueprint_path: Path,
    theorem_library_path: Path,
) -> Dict[str, Dict[str, Any]]:
    if not blueprint_path.exists():
        return {}
    accepted_statement_keys = load_accepted_statement_keys(theorem_library_path)
    blocks = [extract_block(block) for block in split_top_level_blocks(blueprint_path.read_text(encoding="utf-8"))]

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

    def dependency_closure(idx: int) -> List[int]:
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
        if block["statement_key"] in accepted_statement_keys:
            continue
        dependency_cards: List[Tuple[str, str]] = []
        for dep_idx in dependency_closure(idx):
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
        verification_key = compute_verification_key(
            block.get("statement", ""),
            dependency_context,
            block.get("proof", ""),
        )
        current[verification_key] = {
            "index": idx,
            "title": block.get("title", ""),
            "label": block.get("label", ""),
            "statement": block.get("statement", ""),
            "proof": block.get("proof", ""),
            "statement_key": block.get("statement_key", ""),
            "verification_key": verification_key,
            "dependency_indices": dependencies.get(idx, []),
            "dependency_statement_keys": [
                blocks[dep_idx - 1]["statement_key"]
                for dep_idx in dependency_closure(idx)
            ],
        }
    return current


def build_current_scheduler_view(
    blueprint_path: Path,
    theorem_library_path: Path,
    verification_cache_path: Path,
) -> Dict[str, Any]:
    if not blueprint_path.exists():
        return {"blocks": [], "accepted_statement_keys": set()}
    accepted_statement_keys = load_accepted_statement_keys(theorem_library_path)
    blocks = [extract_block(block) for block in split_top_level_blocks(blueprint_path.read_text(encoding="utf-8"))]
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

    cache_payload: Dict[str, Any] = {}
    if verification_cache_path.exists():
        try:
            cache_payload = json.loads(verification_cache_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache_payload = {}
    pairs = cache_payload.get("pairs") or {}
    if not isinstance(pairs, dict):
        pairs = {}
    current_by_statement_key = {
        str(entry.get("statement_key") or ""): entry
        for entry in pairs.values()
        if isinstance(entry, dict) and entry.get("statement_key")
    }

    provisional_indices: set[int] = set()
    wrong_indices: set[int] = set()
    changed = True
    while changed:
        changed = False
        for idx, block in enumerate(blocks, start=1):
            if block["statement_key"] in accepted_statement_keys:
                continue
            if idx in wrong_indices or idx in provisional_indices:
                continue
            current = current_by_statement_key.get(block["statement_key"], {})
            if int(current.get("wrong_verifies", 0) or 0) > 0 and str(current.get("last_result") or "") == "wrong":
                wrong_indices.add(idx)
                changed = True
                continue
            if int(current.get("correct_streak", 0) or 0) <= 0:
                continue
            if all(
                blocks[dep - 1]["statement_key"] in accepted_statement_keys or dep in provisional_indices
                for dep in dependencies.get(idx, [])
            ):
                provisional_indices.add(idx)
                changed = True

    completed_indices = {
        idx for idx, block in enumerate(blocks, start=1)
        if block["statement_key"] in accepted_statement_keys
    } | provisional_indices

    rows: List[Dict[str, Any]] = []
    for idx, block in enumerate(blocks, start=1):
        current = current_by_statement_key.get(block["statement_key"], {})
        blocking_indices = [
            dep for dep in dependencies.get(idx, [])
            if dep not in completed_indices
        ]
        if block["statement_key"] in accepted_statement_keys:
            scheduler_status = "accepted"
        elif idx in wrong_indices:
            scheduler_status = "wrong"
        elif idx in provisional_indices:
            scheduler_status = "provisional"
        elif not blocking_indices:
            scheduler_status = "ready"
        else:
            scheduler_status = "blocked"
        rows.append(
            {
                "index": idx,
                "title": block["title"],
                "label": block["label"],
                "statement_key": block["statement_key"],
                "verification_key": str(current.get("verification_key") or ""),
                "current_pair_streak": int(current.get("correct_streak", 0) or 0),
                "wrong_verifies": int(current.get("wrong_verifies", 0) or 0),
                "infra_verifies": int(current.get("infra_verifies", 0) or 0),
                "last_result": str(current.get("last_result") or ""),
                "last_run_id": str(current.get("last_run_id") or ""),
                "last_verified_at_utc": str(current.get("last_verified_at_utc") or ""),
                "scheduler_status": scheduler_status,
                "blocking_indices": blocking_indices,
                "blocking_titles": [blocks[dep - 1]["title"] for dep in blocking_indices],
            }
        )

    return {
        "blocks": rows,
        "accepted_statement_keys": accepted_statement_keys,
        "dependencies": dependencies,
    }


def latest_current_wrong_from_scheduler_view(
    scheduler_view: Dict[str, Any],
    verification_cache_path: Path,
) -> Optional[Dict[str, Any]]:
    cache_payload: Dict[str, Any] = {}
    if verification_cache_path.exists():
        try:
            cache_payload = json.loads(verification_cache_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache_payload = {}
    pairs = cache_payload.get("pairs") or {}
    if not isinstance(pairs, dict):
        pairs = {}
    wrong_blocks = [
        block for block in (scheduler_view.get("blocks") or [])
        if isinstance(block, dict) and block.get("scheduler_status") == "wrong"
    ]
    wrong_blocks.sort(key=lambda block: int(block.get("index", 0) or 0), reverse=True)
    for block in wrong_blocks:
        entry = pairs.get(str(block.get("verification_key") or ""))
        if not isinstance(entry, dict):
            continue
        report = entry.get("report") or {}
        if not isinstance(report, dict):
            continue
        if report.get("verdict") != "wrong":
            continue
        return {
            "title": block.get("title"),
            "run_id": entry.get("last_run_id"),
            "verification_key": block.get("verification_key"),
            "verdict": "wrong",
            "verification": report,
        }
    return None


def aggregate_current_verification_results(
    current_map: Dict[str, Dict[str, Any]],
    verifier_results_root: Path,
) -> Dict[str, Dict[str, Any]]:
    aggregate: Dict[str, Dict[str, Any]] = {}
    for key, entry in current_map.items():
        aggregate[key] = {
            **entry,
            "correct_run_ids": [],
            "wrong_run_ids": [],
            "infra_run_ids": [],
            "correct_streak": 0,
            "total_correct_verifies": 0,
            "wrong_verifies": 0,
            "infra_verifies": 0,
            "last_result": "",
            "last_run_id": "",
            "last_verified_at_utc": "",
            "report": {},
        }

    if not verifier_results_root.exists():
        return aggregate

    ordered_runs = sorted(
        [p for p in verifier_results_root.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
    )
    for run_dir in ordered_runs:
        state_path = run_dir / "state.json"
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        verification_key = str(state.get("verification_key") or "")
        if verification_key not in aggregate:
            continue
        entry = aggregate[verification_key]
        status = str(state.get("status") or "")
        verdict = str(state.get("verdict") or "")
        verification_payload: Dict[str, Any] = {}
        verification_path = run_dir / "verification.json"
        if verification_path.exists():
            try:
                verification_payload = json.loads(verification_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                verification_payload = {}
            if not verdict:
                verdict = str(verification_payload.get("verdict") or "")
        run_id = run_dir.name
        updated_at = str(state.get("updated_at_utc") or "")

        if status == "succeeded" and verdict == "correct":
            entry["correct_run_ids"].append(run_id)
            entry["correct_streak"] = len(entry["correct_run_ids"])
            entry["total_correct_verifies"] = len(entry["correct_run_ids"])
            entry["last_result"] = "correct"
            entry["last_run_id"] = run_id
            entry["last_verified_at_utc"] = updated_at
            entry["report"] = verification_payload
        elif status == "succeeded" and verdict == "wrong":
            entry["wrong_run_ids"].append(run_id)
            entry["wrong_verifies"] = len(entry["wrong_run_ids"])
            entry["last_result"] = "wrong"
            entry["last_run_id"] = run_id
            entry["last_verified_at_utc"] = updated_at
            entry["report"] = verification_payload
        elif status in {"failed", "timed_out", "interrupted"}:
            entry["infra_run_ids"].append(run_id)
            entry["infra_verifies"] = len(entry["infra_run_ids"])
            entry["last_result"] = "infrastructure_error"
            entry["last_run_id"] = run_id
            entry["last_verified_at_utc"] = updated_at

    return aggregate


def write_verification_cache(
    output_path: Path,
    current_map: Dict[str, Dict[str, Any]],
    aggregate: Dict[str, Dict[str, Any]],
) -> None:
    payload = {
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "current_verification_keys": sorted(current_map),
        "pairs": aggregate,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def refresh_verification_cache_from_results(
    blueprint_path: Path,
    theorem_library_path: Path,
    verifier_results_root: Path,
    output_path: Path,
) -> Dict[str, Dict[str, Any]]:
    current_map = build_current_verification_map(blueprint_path, theorem_library_path)
    aggregate = aggregate_current_verification_results(current_map, verifier_results_root)
    write_verification_cache(output_path, current_map, aggregate)
    return aggregate
