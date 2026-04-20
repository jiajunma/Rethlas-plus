#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    parser.add_argument("--mode", choices=("sequential", "parallel"), default="sequential")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--passes-required", type=int, default=3)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--resume-existing", action="store_true")
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


def main() -> int:
    args = parse_args()
    blueprint_path = args.blueprint.resolve()
    text = blueprint_path.read_text(encoding="utf-8")

    blocks = [extract_block_parts(block) for block in split_top_level_blocks(text)]
    structure = structure_report(blocks)
    dependency_data = build_dependency_data(blocks)

    results_dir = blueprint_path.parent
    output_path = results_dir / "section_verification.json"

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
                    return reports
        return []

    def run_one_pass(pass_index: int, completed_passes: int) -> Dict[str, Any]:
        section_reports: List[Dict[str, Any]] = [
            report
            for report in load_existing_section_reports(pass_index)
            if isinstance(report, dict) and report.get("verdict") == "correct"
        ]
        overall_verdict = "correct"
        existing_indices = {
            report.get("index")
            for report in section_reports
            if isinstance(report, dict)
            and isinstance(report.get("index"), int)
            and report.get("verdict") == "correct"
        }

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

        prefix_markdowns: List[str] = []
        current = []
        for block in blocks:
            current.append(block.markdown.rstrip() + "\n")
            prefix_markdowns.append("\n".join(current).strip() + "\n")

        dependencies: Dict[int, List[int]] = dependency_data["dependencies"]

        def verify_one(idx: int, block: ProofBlock, proof_text: str) -> Dict[str, Any]:
            report: Dict[str, Any] = {
                "index": idx,
                "title": block.title,
                "kind": block.kind,
                "proof_nonblank_lines": block.proof_nonblank_lines,
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
                    if idx not in existing_indices
                    and all(dep in existing_indices for dep in dependencies.get(idx, []))
                ]
                if not ready_indices:
                    remaining_indices = [
                        idx for idx in range(args.start_index, len(blocks) + 1) if idx not in existing_indices
                    ]
                    if remaining_indices:
                        overall_verdict = "blocked_by_dependency"
                    break
                idx = min(ready_indices)
                block = blocks[idx - 1]
                proof_text = prefix_markdowns[idx - 1]
                print(
                    f"[verify_sections] pass starting block {idx}/{len(blocks)}: {block.title}",
                    flush=True,
                )
                try:
                    report = verify_one(idx, block, proof_text)
                except Exception as exc:  # noqa: BLE001
                    report = {
                        "index": idx,
                        "title": block.title,
                        "kind": block.kind,
                        "proof_nonblank_lines": block.proof_nonblank_lines,
                        "verdict": "infrastructure_error",
                        "error": str(exc),
                    }
                    overall_verdict = "infrastructure_error"
                    section_reports.append(report)
                    write_in_progress()
                    print(
                        f"[verify_sections] block {idx} infrastructure_error: {exc}",
                        flush=True,
                    )
                    break

                section_reports.append(report)
                write_in_progress()
                print(
                    f"[verify_sections] block {idx} verdict: {report['verdict']}",
                    flush=True,
                )
                if report["verdict"] != "correct":
                    consecutive_failures += 1
                    overall_verdict = "wrong"
                    if consecutive_failures >= args.max_consecutive_failures:
                        overall_verdict = "return_to_generator"
                        break
                else:
                    consecutive_failures = 0
                    existing_indices.add(idx)
        else:
            max_workers = max(1, args.max_workers)
            while True:
                ready_indices = [
                    idx
                    for idx in range(args.start_index, len(blocks) + 1)
                    if idx not in existing_indices
                    and all(dep in existing_indices for dep in dependencies.get(idx, []))
                ]
                if not ready_indices:
                    remaining_indices = [
                        idx for idx in range(args.start_index, len(blocks) + 1) if idx not in existing_indices
                    ]
                    if remaining_indices and overall_verdict == "correct":
                        overall_verdict = "blocked_by_dependency"
                    break

                batch_indices = ready_indices[:max_workers]
                futures = {}
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for idx in batch_indices:
                        block = blocks[idx - 1]
                        proof_text = prefix_markdowns[idx - 1]
                        futures[executor.submit(verify_one, idx, block, proof_text)] = (idx, block)

                    parallel_reports: Dict[int, Dict[str, Any]] = {}
                    for future in as_completed(futures):
                        idx, block = futures[future]
                        try:
                            parallel_reports[idx] = future.result()
                        except Exception as exc:  # noqa: BLE001
                            parallel_reports[idx] = {
                                "index": idx,
                                "title": block.title,
                                "kind": block.kind,
                                "proof_nonblank_lines": block.proof_nonblank_lines,
                                "verdict": "infrastructure_error",
                                "error": str(exc),
                            }
                            if overall_verdict == "correct":
                                overall_verdict = "infrastructure_error"

                for idx in sorted(parallel_reports):
                    report = parallel_reports[idx]
                    section_reports.append(report)
                    if report.get("verdict") == "correct":
                        existing_indices.add(idx)
                    elif overall_verdict == "correct":
                        overall_verdict = "wrong"
                if overall_verdict not in {"correct"}:
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
