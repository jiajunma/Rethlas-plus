#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theorem-library", type=Path, required=True)
    parser.add_argument("--order-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--basename", default="blueprint_from_theorem_library")
    return parser.parse_args()


def compute_statement_key(statement: str) -> str:
    return hashlib.sha256(statement.encode("utf-8")).hexdigest()


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


def extract_statement(block: str) -> str:
    import re

    statement_match = re.search(r"^## statement\s*$", block, flags=re.MULTILINE)
    proof_match = re.search(r"^## proof\s*$", block, flags=re.MULTILINE)
    if statement_match and proof_match and statement_match.end() <= proof_match.start():
        return block[statement_match.end():proof_match.start()].strip()
    return ""


def block_from_entry(entry: Dict[str, Any]) -> str:
    block_markdown = str(entry.get("block_markdown") or "").strip()
    if block_markdown:
        return block_markdown + "\n"
    title = str(entry.get("title") or "").strip()
    statement = str(entry.get("statement") or "").strip()
    proof = str(entry.get("proof_markdown") or "").strip()
    lines = [title, "", "## statement", statement, "", "## proof", proof]
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    theorem_library_path = args.theorem_library.resolve()
    order_source_path = args.order_source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    theorem_library = json.loads(theorem_library_path.read_text(encoding="utf-8"))
    accepted = theorem_library.get("accepted") or {}
    if not isinstance(accepted, dict):
        raise SystemExit("theorem library does not contain an 'accepted' object")

    order_blocks = split_top_level_blocks(order_source_path.read_text(encoding="utf-8"))
    order_keys = [
        compute_statement_key(extract_statement(block))
        for block in order_blocks
        if extract_statement(block)
    ]

    ordered_blocks: List[str] = []
    used_keys = set()
    for statement_key in order_keys:
        entry = accepted.get(statement_key)
        if not isinstance(entry, dict):
            continue
        ordered_blocks.append(block_from_entry(entry))
        used_keys.add(statement_key)

    for statement_key, entry in accepted.items():
        if statement_key in used_keys or not isinstance(entry, dict):
            continue
        ordered_blocks.append(block_from_entry(entry))

    md_path = output_dir / f"{args.basename}.md"
    tex_path = output_dir / f"{args.basename}.tex"
    pdf_path = output_dir / f"{args.basename}.pdf"

    md_path.write_text("\n\n".join(block.strip() for block in ordered_blocks).strip() + "\n", encoding="utf-8")

    subprocess.run(
        [
            "pandoc",
            str(md_path),
            "--standalone",
            "--from",
            "markdown+tex_math_single_backslash+tex_math_dollars",
            "-t",
            "latex",
            "-o",
            str(tex_path),
        ],
        check=True,
    )
    subprocess.run(
        [
            "xelatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            str(tex_path.name),
        ],
        cwd=output_dir,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    subprocess.run(
        [
            "xelatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            str(tex_path.name),
        ],
        cwd=output_dir,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )

    print(
        json.dumps(
            {
                "theorem_library_path": str(theorem_library_path),
                "order_source_path": str(order_source_path),
                "output_markdown": str(md_path),
                "output_tex": str(tex_path),
                "output_pdf": str(pdf_path),
                "accepted_entries_used": len(ordered_blocks),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
