#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


VERIFY_URL = "http://127.0.0.1:8091/verify"
DEFAULT_TIMEOUT_SECONDS = 3600


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
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--verify-url", default=VERIFY_URL)
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

    if not blocks:
        issues.append({"location": "blueprint", "issue": "No top-level proof blocks found."})
        return {
            "summary": "No top-level blocks.",
            "issues": issues,
            "block_titles": [],
        }

    for block in blocks:
        if not block.statement:
            issues.append({"location": block.title, "issue": "Missing `## statement` section."})
        if not block.proof:
            issues.append({"location": block.title, "issue": "Missing `## proof` section."})
        if block.proof_nonblank_lines > 30:
            issues.append(
                {
                    "location": block.title,
                    "issue": f"Proof has {block.proof_nonblank_lines} non-blank lines; exceeds 30-line target.",
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
        "block_titles": [block.title for block in blocks],
    }


def call_verifier(
    verify_url: str,
    statement: str,
    proof: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    response = requests.post(
        verify_url,
        json={"statement": statement, "proof": proof},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Verifier response must be a JSON object.")
    return payload


def main() -> int:
    args = parse_args()
    blueprint_path = args.blueprint.resolve()
    text = blueprint_path.read_text(encoding="utf-8")

    blocks = [extract_block_parts(block) for block in split_top_level_blocks(text)]
    structure = structure_report(blocks)

    results_dir = blueprint_path.parent
    output_path = results_dir / "section_verification.json"

    section_reports: List[Dict[str, Any]] = []
    overall_verdict = "correct"

    if structure["issues"]:
        overall_verdict = "wrong"
    else:
        prefix_markdown: List[str] = []
        for idx, block in enumerate(blocks, start=1):
            prefix_markdown.append(block.markdown.rstrip() + "\n")
            report: Dict[str, Any] = {
                "index": idx,
                "title": block.title,
                "kind": block.kind,
                "proof_nonblank_lines": block.proof_nonblank_lines,
            }
            try:
                verifier_payload = call_verifier(
                    verify_url=args.verify_url,
                    statement=block.statement,
                    proof="\n".join(prefix_markdown).strip() + "\n",
                    timeout_seconds=args.timeout_seconds,
                )
                report["verification"] = verifier_payload
                report["verdict"] = verifier_payload.get("verdict", "wrong")
            except Exception as exc:  # noqa: BLE001
                report["verdict"] = "infrastructure_error"
                report["error"] = str(exc)
                overall_verdict = "infrastructure_error"
                section_reports.append(report)
                break

            section_reports.append(report)
            if report["verdict"] != "correct":
                overall_verdict = "wrong"
                break

    payload = {
        "blueprint_path": str(blueprint_path),
        "structure_report": structure,
        "section_reports": section_reports,
        "overall_verdict": overall_verdict,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return 0 if overall_verdict == "correct" else 1


if __name__ == "__main__":
    raise SystemExit(main())
