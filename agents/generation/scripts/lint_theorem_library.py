#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


LABEL_PATTERN = re.compile(r"(lem:[A-Za-z0-9_]+|prop:[A-Za-z0-9_]+|thm:[A-Za-z0-9_]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("blueprint", type=Path)
    parser.add_argument("--theorem-library", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


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


def compute_statement_key(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


def extract_block(block: str, index: int) -> Dict[str, Any]:
    lines = block.splitlines()
    title = lines[0].strip() if lines else ""
    label_match = LABEL_PATTERN.search(title)
    label = label_match.group(1) if label_match else ""
    statement_match = re.search(r"^## statement\s*$", block, flags=re.MULTILINE)
    proof_match = re.search(r"^## proof\s*$", block, flags=re.MULTILINE)
    statement = ""
    proof = ""
    if statement_match and proof_match and statement_match.end() <= proof_match.start():
        statement = block[statement_match.end():proof_match.start()].strip()
        proof = block[proof_match.end():].strip()
    return {
        "index": index,
        "title": title,
        "label": label,
        "statement": statement,
        "statement_key": compute_statement_key(statement),
        "proof": proof,
        "dep_labels": sorted(set(LABEL_PATTERN.findall(proof))),
    }


def main() -> int:
    args = parse_args()
    blueprint_path = args.blueprint.resolve()
    theorem_library_path = (
        args.theorem_library.resolve()
        if args.theorem_library is not None
        else blueprint_path.parent / "theorem_library.json"
    )

    blocks = [
        extract_block(block, idx)
        for idx, block in enumerate(split_top_level_blocks(blueprint_path.read_text(encoding="utf-8")), start=1)
    ]
    labels_by_index = {block["index"]: block["label"] for block in blocks if block["label"]}
    indices_by_label = {block["label"]: block["index"] for block in blocks if block["label"]}
    for block in blocks:
        block["dep_indices"] = sorted(
            {
                indices_by_label[label]
                for label in block["dep_labels"]
                if label in indices_by_label and indices_by_label[label] != block["index"]
            }
        )

    theorem_library = {}
    if theorem_library_path.exists():
        payload = json.loads(theorem_library_path.read_text(encoding="utf-8"))
        theorem_library = payload.get("accepted", {}) if isinstance(payload, dict) else {}
    theorem_library = theorem_library if isinstance(theorem_library, dict) else {}

    accepted_library_entries = {
        key: value
        for key, value in theorem_library.items()
        if isinstance(value, dict) and value.get("accepted") is True
    }

    accepted_current_indices = {
        block["index"] for block in blocks if block["statement_key"] in accepted_library_entries
    }

    accepted_dependency_issues: List[Dict[str, Any]] = []
    for block in blocks:
        if block["index"] not in accepted_current_indices:
            continue
        missing = [idx for idx in block["dep_indices"] if idx not in accepted_current_indices]
        if missing:
            accepted_dependency_issues.append(
                {
                    "index": block["index"],
                    "title": block["title"],
                    "label": block["label"],
                    "missing_dep_indices": missing,
                    "missing_dep_titles": [blocks[idx - 1]["title"] for idx in missing],
                }
            )

    duplicates_by_title: Dict[str, List[Dict[str, Any]]] = {}
    duplicates_by_label: Dict[str, List[Dict[str, Any]]] = {}
    stale_entries: List[Dict[str, Any]] = []
    current_statement_keys = {block["statement_key"] for block in blocks}
    for key, entry in accepted_library_entries.items():
        title = str(entry.get("title") or "")
        label_match = LABEL_PATTERN.search(title)
        label = label_match.group(1) if label_match else ""
        if title:
            duplicates_by_title.setdefault(title, []).append(entry)
        if label:
            duplicates_by_label.setdefault(label, []).append(entry)
        if key not in current_statement_keys:
            stale_entries.append(
                {
                    "title": title,
                    "label": label,
                    "statement_key": key,
                    "correct_verify_count": entry.get("correct_verify_count"),
                }
            )

    duplicate_title_issues = [
        {
            "title": title,
            "count": len(entries),
            "statement_keys": [entry.get("statement_key") for entry in entries],
        }
        for title, entries in duplicates_by_title.items()
        if len(entries) > 1
    ]
    duplicate_label_issues = [
        {
            "label": label,
            "count": len(entries),
            "titles": [entry.get("title") for entry in entries],
            "statement_keys": [entry.get("statement_key") for entry in entries],
        }
        for label, entries in duplicates_by_label.items()
        if len(entries) > 1
    ]

    report = {
        "blueprint_path": str(blueprint_path),
        "theorem_library_path": str(theorem_library_path),
        "accepted_current_count": len(accepted_current_indices),
        "accepted_library_count": len(accepted_library_entries),
        "accepted_dependency_issues": accepted_dependency_issues,
        "duplicate_title_issues": duplicate_title_issues,
        "duplicate_label_issues": duplicate_label_issues,
        "stale_accepted_entries": stale_entries,
    }

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
