#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


CODEX_BIN = "codex"
CODEX_MODEL = "gpt-5.4"
CODEX_REASONING_EFFORT = "high"
MAX_REPAIR_ATTEMPTS = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--statement", required=True)
    parser.add_argument("--proof-file", type=Path, required=True)
    parser.add_argument("--verification-file", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip()}\n"


def bullet_findings(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "- None."
    lines = []
    for item in items:
        loc = item.get("location", "unknown")
        issue = item.get("issue", "")
        lines.append(f"- **{loc}**: {issue}")
    return "\n".join(lines)


def build_summary(statement: str, proof_text: str, verification: Dict[str, Any]) -> str:
    report = verification.get("verification_report", {})
    verdict = verification.get("verdict", "wrong")
    repair_hints = verification.get("repair_hints", "")

    summary_parts = [
        "# Verification Summary",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        section("Statement", statement),
        section("Summary", report.get("summary", "")),
        section("Critical Errors", bullet_findings(report.get("critical_errors", []))),
        section("Gaps", bullet_findings(report.get("gaps", []))),
        section("Repair Hints", repair_hints or "No repair hints."),
        section("Proof", proof_text),
    ]
    return "\n".join(summary_parts).strip() + "\n"


def try_build_pdf(summary_md: Path) -> Dict[str, Any]:
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if not pandoc or not xelatex:
        return {
            "pdf_generated": False,
            "pdf_error": "pandoc or xelatex not available",
        }

    pdf_path = summary_md.with_suffix(".pdf")
    try:
        subprocess.run(
            [
                pandoc,
                "-f",
                "markdown+tex_math_dollars+tex_math_single_backslash",
                str(summary_md),
                "--pdf-engine",
                xelatex,
                "-V",
                "geometry:margin=1in",
                "-o",
                str(pdf_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return {
            "pdf_generated": True,
            "pdf_path": str(pdf_path),
        }
    except subprocess.CalledProcessError as exc:
        return {
            "pdf_generated": False,
            "pdf_error": exc.stderr or exc.stdout or "unknown PDF generation error",
        }


def try_llm_repair_summary(summary_md: Path, pdf_error: str) -> Dict[str, Any]:
    codex = shutil.which(CODEX_BIN)
    if not codex:
        return {
            "repair_attempted": False,
            "repair_error": "codex not available",
        }

    work_dir = summary_md.parent
    prompt = f"""
You are repairing a markdown file only so that pandoc + xelatex can compile it to PDF.

File to edit in place:
- {summary_md.name}

Compilation error:
{pdf_error}

Rules:
- Only change markdown / LaTeX formatting needed for PDF compilation.
- Do not change the verdict.
- Do not delete or rewrite findings.
- Do not change the mathematical substance of the proof.
- Preserve all sections and all listed errors/gaps.
- Prefer minimal edits.
"""

    try:
        completed = subprocess.run(
            [
                codex,
                "exec",
                "-C",
                str(work_dir),
                "-m",
                CODEX_MODEL,
                "--config",
                f"model_reasoning_effort={CODEX_REASONING_EFFORT}",
                "--dangerously-bypass-approvals-and-sandbox",
                prompt,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "repair_attempted": True,
            "repair_error": str(exc),
        }

    if completed.returncode != 0:
        return {
            "repair_attempted": True,
            "repair_error": completed.stderr or completed.stdout or f"codex exited {completed.returncode}",
        }

    return {
        "repair_attempted": True,
        "repair_error": "",
    }


def main() -> int:
    args = parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)

    proof_text = args.proof_file.read_text(encoding="utf-8")
    verification = load_json(args.verification_file)

    summary_text = build_summary(args.statement, proof_text, verification)
    summary_md = args.results_dir / "summary.md"
    summary_md.write_text(summary_text, encoding="utf-8")

    pdf_info = try_build_pdf(summary_md)
    repair_log: List[Dict[str, Any]] = []
    repair_attempts = 0
    while not pdf_info.get("pdf_generated", False) and repair_attempts < MAX_REPAIR_ATTEMPTS:
        repair_attempts += 1
        repair_result = try_llm_repair_summary(summary_md, str(pdf_info.get("pdf_error", "")))
        repair_result["attempt"] = repair_attempts
        repair_log.append(repair_result)
        if repair_result.get("repair_error"):
            break
        pdf_info = try_build_pdf(summary_md)

    summary_meta = {
        "summary_markdown": str(summary_md),
        **pdf_info,
        "repair_attempts": repair_attempts,
        "repair_log": repair_log,
    }
    (args.results_dir / "summary_meta.json").write_text(
        json.dumps(summary_meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
