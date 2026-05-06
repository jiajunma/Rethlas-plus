from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEGACY_RESULTS = REPO_ROOT / "agents" / "generation" / "results" / "real_induced_orbit_problem_B_general"
DEFAULT_WORKSPACE = REPO_ROOT / "tests" / "manual_runs" / "real_induced_orbit_problem_B_general_reverify_20260504"

LABEL_REF_PATTERN = re.compile(r"`((?:def|ext|lem|prop|thm):[A-Za-z0-9_.-]+)`")
CLI_PREFIX = ["uv", "run", "--python", "3.11", "python", "-m", "cli.main"]
MANUAL_EXTRA_DEPS: dict[str, list[str]] = {
    "lem:symmetric_package_move": [
        "lem:block_form_for_x0_plus_u",
        "lem:signed_chain_model",
    ],
    "lem:alternating_nondegenerate_package_move": [
        "lem:block_form_for_x0_plus_u",
        "lem:signed_chain_model",
    ],
    "lem:alternating_radical_package_move": [
        "lem:block_form_for_x0_plus_u",
        "lem:signed_chain_model",
    ],
    "lem:highest_degree_power_formula": [
        "lem:block_form_for_x0_plus_u",
    ],
}


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def load_legacy_entries(theorem_library_path: Path) -> list[dict[str, Any]]:
    body = json.loads(theorem_library_path.read_text(encoding="utf-8"))
    accepted = body.get("accepted", {})
    if not isinstance(accepted, dict):
        raise ValueError(f"unexpected theorem library format at {theorem_library_path}")
    out: list[dict[str, Any]] = []
    for statement_key, entry in accepted.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("accepted"):
            continue
        label = entry.get("label")
        kind = entry.get("kind")
        statement = entry.get("statement")
        proof = entry.get("proof_markdown")
        if not all(isinstance(x, str) and x.strip() for x in (label, kind, statement, proof)):
            continue
        out.append(
            {
                "statement_key": statement_key,
                "label": label.strip(),
                "kind": kind.strip(),
                "statement": statement.strip(),
                "proof": proof.strip(),
                "remark": (entry.get("title") or "").strip(),
                "source_note": f"Imported from legacy theorem library: {theorem_library_path}",
                "dependency_labels": list(entry.get("dependency_labels") or []),
            }
        )
    return out


def normalize_label(label: str) -> str:
    prefix, sep, slug = label.partition(":")
    if not sep:
        raise ValueError(f"label missing prefix: {label}")
    if label == "thm:main":
        return "thm:real_induced_orbit_main"
    if label == "lem:main":
        return "lem:real_induced_orbit_main_helper"
    if label == "prop:main":
        return "prop:real_induced_orbit_main_helper"
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9_]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        raise ValueError(f"label slug became empty after normalization: {label}")
    return f"{prefix}:{slug}"


def remap_labels(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    label_map: dict[str, str] = {}
    used: set[str] = set()
    for entry in entries:
        old = entry["label"]
        new = normalize_label(old)
        if new in used and label_map.get(old) != new:
            raise ValueError(f"normalized label collision: {old} -> {new}")
        label_map[old] = new
        used.add(new)
    remapped: list[dict[str, Any]] = []
    for entry in entries:
        clone = dict(entry)
        clone["legacy_label"] = entry["label"]
        clone["label"] = label_map[entry["label"]]
        deps = [label_map.get(lbl, normalize_label(lbl)) for lbl in entry["dependency_labels"]]
        deps.extend(label_map.get(lbl, normalize_label(lbl)) for lbl in MANUAL_EXTRA_DEPS.get(entry["label"], []))
        clone["dependency_labels"] = sorted(dict.fromkeys(deps))
        remapped.append(clone)
    return remapped, label_map


def normalize_proof_refs(proof: str, dependency_labels: list[str], label_map: dict[str, str]) -> str:
    text = proof
    for old_label, new_label in sorted(label_map.items()):
        escaped_old = re.escape(old_label)
        text = re.sub(rf"`{escaped_old}`", rf"\\ref{{{new_label}}}", text)
        text = re.sub(rf"\b(?:lemma|proposition|theorem)\s+`{escaped_old}`", rf"\\ref{{{new_label}}}", text)
        text = text.replace(f"\\ref{{{old_label}}}", f"\\ref{{{new_label}}}")
    for label in sorted({lbl for lbl in dependency_labels if isinstance(lbl, str)}):
        escaped = re.escape(label)
        text = re.sub(rf"`{escaped}`", rf"\\ref{{{label}}}", text)
    return text


def ensure_declared_refs(entry: dict[str, Any], label_map: dict[str, str]) -> dict[str, Any]:
    proof = normalize_proof_refs(entry["proof"], entry["dependency_labels"], label_map)
    if entry["label"] == "lem:alternating_nondegenerate_package_move":
        proof = proof.replace(
            "As in the previous lemma,\n\\[\nx(u_k^\\pm)=x_0u_k^\\pm+A(u_k^\\pm).\n\\]",
            "By \\ref{lem:block_form_for_x0_plus_u}, for every \\(v\\in V_0\\) we have\n\\[\nx(v)=x_0v+A(v),\n\\]\nso in particular\n\\[\nx(u_k^\\pm)=x_0u_k^\\pm+A(u_k^\\pm).\n\\]\nBy \\ref{lem:signed_chain_model}, the standard invariant chain form pairs a top vector only with the complementary bottom vector in its Jordan chain."
        )
    if entry["label"] == "lem:alternating_radical_package_move":
        proof = proof.replace(
            "For \\(0\\le k\\le d-2\\), the same pairing argument as above gives\n\\[\nA(x_0^k t_+)=A(x_0^k t_-)=0.\n\\]",
            "By \\ref{lem:block_form_for_x0_plus_u}, for every \\(v\\in V_0\\) and \\(y'\\in Y\\) we have\n\\[\n\\langle y',A(v)\\rangle=-\\langle B(y'),v\\rangle.\n\\]\nBy \\ref{lem:signed_chain_model}, the standard invariant chain form pairs a top vector only with the complementary bottom vector in its Jordan chain. Therefore for \\(0\\le k\\le d-2\\),\n\\[\nA(x_0^k t_+)=A(x_0^k t_-)=0.\n\\]"
        )
    if entry["label"] == "lem:symmetric_package_move":
        proof = proof.replace(
            "For \\(0\\le k\\le d-2\\), put \\(u_k:=x_0^k t\\). Then\n\\[\nx(u_k)=x_0u_k+A(u_k).\n\\]",
            "By \\ref{lem:block_form_for_x0_plus_u}, for every \\(v\\in V_0\\) we have\n\\[\nx(v)=x_0v+A(v).\n\\]\nThus for \\(0\\le k\\le d-2\\), with \\(u_k:=x_0^k t\\),\n\\[\nx(u_k)=x_0u_k+A(u_k).\n\\]"
        )
    if entry["label"] == "lem:block_form_for_x0_plus_u":
        proof = proof.replace(
            "Because \\(x\\in x_0+\\mathfrak u\\), it kills \\(X\\), induces \\(x_0\\) on \\(V_0=X^\\perp/X\\), and sends \\(Y\\) into \\(X^\\perp=X\\oplus V_0\\). So \\(x\\) has the displayed block form for unique linear maps \\(A,B,C\\).",
            "Because \\(x\\in x_0+\\mathfrak u\\), write \\(x=x_0+n\\) with \\(n\\in\\mathfrak u\\). By definition of the nilradical \\(\\mathfrak u\\) of the maximal parabolic stabilizing \\(X\\), every \\(n\\in\\mathfrak u\\) kills \\(X\\), maps \\(V\\) into \\(X^\\perp\\), and induces \\(0\\) on the quotient \\(X^\\perp/X\\). Therefore \\(x\\) kills \\(X\\), maps \\(Y\\) into \\(X^\\perp\\), and induces the same endomorphism as \\(x_0\\) on \\(X^\\perp/X=V_0\\). Since the decomposition \\(V=X\\oplus V_0\\oplus Y\\) is fixed, this gives unique linear maps \\(A:V_0\\to X\\), \\(B:Y\\to V_0\\), and \\(C:Y\\to X\\) with the displayed block form."
        )
        proof = proof.replace(
            "Taking \\(u=y_1\\), \\(v=y_2\\in Y\\) gives\n\\[\n\\langle y_1,Cy_2\\rangle+\\epsilon\\,\\langle y_2,Cy_1\\rangle=0,\n\\]\nbecause \\(B(y_i)\\in V_0\\) and \\(V_0\\perp Y\\). This is the stated condition on \\(C\\).",
            "Taking \\(u=y_1\\), \\(v=y_2\\in Y\\) gives\n\\[\n\\langle Cy_1,y_2\\rangle+\\langle y_1,Cy_2\\rangle=0,\n\\]\nbecause \\(B(y_i)\\in V_0\\) and \\(V_0\\perp Y\\). Since the ambient form has parity \\(\\epsilon\\), we have\n\\[\n\\langle Cy_1,y_2\\rangle=\\epsilon\\,\\langle y_2,Cy_1\\rangle.\n\\]\nSubstituting this identity yields\n\\[\n\\langle y_1,Cy_2\\rangle+\\epsilon\\,\\langle y_2,Cy_1\\rangle=0,\n\\]\nwhich is the stated condition on \\(C\\)."
        )
    if entry["label"] == "lem:highest_degree_power_formula":
        proof = (
            "By \\ref{lem:block_form_for_x0_plus_u}, for every \\(y\\in Y\\) we have\n"
            "\\[\nxy=B(y)+C(y),\n\\]\n"
            "with \\(B(y)\\in V_0\\), \\(C(y)\\in X\\), while \\(x\\) kills \\(X\\) and satisfies\n"
            "\\[\nx(v)=x_0v+A(v)\\qquad(v\\in V_0).\n\\]\n"
            "Therefore\n"
            "\\[\nx^2y=x(B(y))+x(C(y))=x_0B(y)+A(B(y)),\n\\]\n"
            "which is the case \\(k=1\\).\n\n"
            "Assume now that for some \\(k\\ge 1\\),\n"
            "\\[\nx^{k+1}y=x_0^kB(y)+A\\!\\bigl(x_0^{k-1}B(y)\\bigr).\n\\]\n"
            "The second summand lies in \\(X\\), so applying \\(x\\) and using again that \\(x\\) kills \\(X\\) gives\n"
            "\\[\nx^{k+2}y\n=x\\!\\bigl(x_0^kB(y)\\bigr)\n=x_0^{k+1}B(y)+A\\!\\bigl(x_0^kB(y)\\bigr).\n\\]\n"
            "Thus the formula holds for all \\(k\\ge 1\\).\n\n"
            "If \\(d\\) is the largest row length in \\(D_0\\), then every Jordan chain of \\(x_0\\) on \\(V_0\\) has length at most \\(d\\), so \\(x_0^d=0\\) on \\(V_0\\). Applying the proved formula with \\(k=d\\) and \\(k=d+1\\) yields\n"
            "\\[\nx^{d+1}y=A\\!\\bigl(x_0^{d-1}B(y)\\bigr),\\qquad x^{d+2}y=0.\n\\]"
        )
    referenced = set(re.findall(r"\\ref\{([^}]+)\}", proof))
    missing = [lbl for lbl in entry["dependency_labels"] if lbl not in referenced]
    if missing:
        prelude = "Dependencies used in this imported proof: " + ", ".join(f"\\ref{{{lbl}}}" for lbl in missing) + ".\n\n"
        proof = prelude + proof
    clone = dict(entry)
    clone["proof"] = proof
    return clone


def topo_sort(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_label = {entry["label"]: entry for entry in entries}
    indegree = {label: 0 for label in by_label}
    outgoing: dict[str, list[str]] = {label: [] for label in by_label}
    for entry in entries:
        label = entry["label"]
        for dep in entry["dependency_labels"]:
            if dep not in by_label:
                continue
            indegree[label] += 1
            outgoing[dep].append(label)
    queue = deque(sorted([label for label, deg in indegree.items() if deg == 0]))
    order: list[str] = []
    while queue:
        label = queue.popleft()
        order.append(label)
        for nxt in sorted(outgoing[label]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(entries):
        remaining = [label for label, deg in indegree.items() if deg > 0]
        raise ValueError(f"dependency cycle among imported entries: {remaining}")
    return [by_label[label] for label in order]


def init_workspace(workspace: Path, *, force: bool) -> None:
    if force and workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*CLI_PREFIX, "--workspace", str(workspace), "init"]
    if force:
        cmd.append("--force")
    result = run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"rethlas init failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def publish_entry(workspace: Path, entry: dict[str, Any]) -> str:
    env = os.environ.copy()
    env["RETHLAS_PUBLISH_POLL_TIMEOUT_S"] = "0"
    cmd = [
        *CLI_PREFIX,
        "--workspace",
        str(workspace),
        "add-node",
        "--label",
        entry["label"],
        "--kind",
        entry["kind"],
        "--statement",
        entry["statement"],
        "--proof",
        entry["proof"],
        "--remark",
        entry["remark"],
        "--source-note",
        entry["source_note"],
        "--actor",
        "user:legacy-import",
    ]
    result = run(cmd, cwd=REPO_ROOT, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"add-node failed for {entry['label']}:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    match = re.search(r"published ([0-9T.\-a-f]+) ->", result.stdout)
    if not match:
        raise RuntimeError(f"could not parse event id from add-node output for {entry['label']}:\n{result.stdout}")
    return match.group(1)

def rebuild_workspace(workspace: Path) -> None:
    result = run([*CLI_PREFIX, "--workspace", str(workspace), "rebuild"], cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"rethlas rebuild failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def verify_once(workspace: Path, label: str) -> dict[str, Any]:
    result = run(
        [
            *CLI_PREFIX,
            "--workspace",
            str(workspace),
            "verifier",
            "--target",
            label,
            "--silent-timeout-s",
            "1800",
            "--actor",
            "verifier:legacy-reverify",
        ],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        return {"label": label, "status": "cli_failed", "stdout": result.stdout, "stderr": result.stderr}
    match = re.search(r"published ([0-9T.\-a-f]+) verdict=([a-z]+)", result.stdout)
    if not match:
        return {"label": label, "status": "no_verdict_event", "stdout": result.stdout, "stderr": result.stderr}
    event_id, verdict = match.group(1), match.group(2)
    rebuild_workspace(workspace)
    return {"event_id": event_id, "verdict": verdict}


def read_workspace_node_snapshot(workspace: Path) -> list[dict[str, Any]]:
    import kuzu

    db_path = workspace / "knowledge_base" / "dag.kz"
    db = kuzu.Database(str(db_path), read_only=True)
    conn = kuzu.Connection(db)
    try:
        res = conn.execute(
            """
            MATCH (n:Node)
            RETURN n.label, n.kind, n.pass_count, n.repair_count,
                   n.statement_hash, n.verification_hash,
                   n.verification_report, n.repair_hint
            ORDER BY n.label
            """
        )
        out = []
        while res.has_next():
            row = res.get_next()
            out.append(
                {
                    "label": row[0],
                    "kind": row[1],
                    "pass_count": int(row[2]),
                    "repair_count": int(row[3]),
                    "statement_hash": row[4],
                    "verification_hash": row[5],
                    "verification_report": row[6] or "",
                    "repair_hint": row[7] or "",
                }
            )
        return out
    finally:
        del conn
        del db


def write_run_summary(workspace: Path, summary: dict[str, Any]) -> None:
    out_path = workspace / "legacy_reverify_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import legacy theorem library into a fresh Rethlas-plus workspace and reverify it.")
    parser.add_argument("--legacy-results", default=str(DEFAULT_LEGACY_RESULTS))
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--desired-pass-count", type=int, default=3)
    args = parser.parse_args()

    legacy_results = Path(args.legacy_results).resolve()
    theorem_library_path = legacy_results / "theorem_library.json"
    if not theorem_library_path.is_file():
        raise SystemExit(f"missing theorem library at {theorem_library_path}")

    entries = load_legacy_entries(theorem_library_path)
    entries, label_map = remap_labels(entries)
    entries = [ensure_declared_refs(entry, label_map) for entry in entries]
    ordered = topo_sort(entries)

    workspace = Path(args.workspace).resolve()
    init_workspace(workspace, force=args.force)
    for entry in ordered:
        publish_entry(workspace, entry)
    rebuild_workspace(workspace)
    verify_results: dict[str, Any] = {entry["label"]: [] for entry in ordered}
    for round_index in range(1, args.desired_pass_count + 1):
        for entry in ordered:
            label = entry["label"]
            previous = verify_results[label]
            if previous and previous[-1].get("verdict") != "accepted":
                continue
            result = verify_once(workspace, label)
            result["round"] = round_index
            verify_results[label].append(result)
            if result.get("verdict") != "accepted":
                break
    summary = {
        "workspace": str(workspace),
        "legacy_results": str(legacy_results),
        "desired_pass_count": args.desired_pass_count,
        "label_map": label_map,
        "imported_labels": [entry["label"] for entry in ordered],
        "verify_results": verify_results,
        "node_snapshot": read_workspace_node_snapshot(workspace),
    }
    write_run_summary(workspace, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
