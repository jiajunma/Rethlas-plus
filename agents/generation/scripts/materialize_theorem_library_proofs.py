#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("blueprint", type=Path)
    parser.add_argument("--theorem-library", type=Path, required=True)
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
    return {
        "title": title,
        "statement": statement,
        "statement_key": compute_statement_key(statement),
        "proof_markdown": proof,
        "proof_nonblank_lines": sum(1 for line in proof.splitlines() if line.strip()),
        "block_markdown": block.strip() + "\n",
    }


def main() -> int:
    args = parse_args()
    blueprint_path = args.blueprint.resolve()
    theorem_library_path = args.theorem_library.resolve()
    output_path = args.output.resolve() if args.output is not None else theorem_library_path

    blueprint_text = blueprint_path.read_text(encoding="utf-8")
    theorem_library = json.loads(theorem_library_path.read_text(encoding="utf-8"))
    accepted = theorem_library.get("accepted") or {}
    if not isinstance(accepted, dict):
        raise SystemExit("theorem library does not contain an 'accepted' object")

    blocks = [extract_block(block) for block in split_top_level_blocks(blueprint_text)]
    blocks_by_statement_key = {
        block["statement_key"]: block
        for block in blocks
        if block["statement_key"]
    }

    updated = 0
    missing: List[str] = []
    for statement_key, entry in accepted.items():
        if not isinstance(entry, dict):
            continue
        block = blocks_by_statement_key.get(statement_key)
        if block is None:
            missing.append(str(entry.get("title") or statement_key))
            continue
        entry["proof_markdown"] = block["proof_markdown"]
        entry["proof_nonblank_lines"] = block["proof_nonblank_lines"]
        entry["block_markdown"] = block["block_markdown"]
        entry["proof_source_path"] = str(blueprint_path)
        entry["proof_source_title"] = block["title"]
        updated += 1

    theorem_library["updated_at_utc"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
    output_path.write_text(json.dumps(theorem_library, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "blueprint_path": str(blueprint_path),
                "theorem_library_path": str(theorem_library_path),
                "output_path": str(output_path),
                "updated_entries": updated,
                "missing_entries": missing,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
