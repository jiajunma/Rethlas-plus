"""Read-only CLI for Phase 3 referee review artifacts."""

from __future__ import annotations

import argparse
import json
import sys

from cli.workspace import ensure_initialised, workspace_paths
from common.phase3.artifacts import list_reviews
from cli.review_repair import run_repair_command


def _find_review(rows: list[dict], review_id: str) -> dict | None:
    for row in rows:
        payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
        rid = payload.get("review_id", "") or row.get("event_id", "")
        if rid == review_id or row.get("event_id", "") == review_id:
            return row
    return None


def _report_payload(row: dict) -> dict:
    payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
    report = payload.get("report", {})
    return report if isinstance(report, dict) else {}


def _review_graph(report: dict) -> dict:
    nodes = report.get("theorem_nodes", [])
    edges = report.get("theorem_dependency_edges", [])
    notes = report.get("node_location_notes", [])
    return {
        "theorem_nodes": nodes if isinstance(nodes, list) else [],
        "theorem_dependency_edges": edges if isinstance(edges, list) else [],
        "node_location_notes": notes if isinstance(notes, list) else [],
    }


def _mermaid_id(label: str, seen: dict[str, str]) -> str:
    if label in seen:
        return seen[label]
    ident = "n" + str(len(seen) + 1)
    seen[label] = ident
    return ident


def _mermaid_label(node: dict) -> str:
    label = str(node.get("label", ""))
    kind = str(node.get("kind", ""))
    title = str(node.get("title", "") or node.get("statement", "") or label)
    text = f"{label}\\n{kind}\\n{title}" if kind else f"{label}\\n{title}"
    return text.replace('"', "'")


def _dot_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_graph_text(graph: dict) -> None:
    notes_by_label: dict[str, list[dict]] = {}
    for note in graph["node_location_notes"]:
        if isinstance(note, dict) and isinstance(note.get("label"), str):
            notes_by_label.setdefault(note["label"], []).append(note)
    sys.stdout.write("# theorem_nodes\n")
    for node in graph["theorem_nodes"]:
        if not isinstance(node, dict):
            continue
        label = str(node.get("label", ""))
        kind = str(node.get("kind", ""))
        title = str(node.get("title", "") or node.get("statement", ""))[:160]
        status = str(node.get("status", ""))
        extraction_kind = str(node.get("extraction_kind", ""))
        source_locator = str(node.get("source_locator", ""))
        sys.stdout.write(
            "\t".join([label, kind, status, extraction_kind, source_locator, title])
            + "\n"
        )
        source_note = str(node.get("source_note", ""))
        if source_note:
            sys.stdout.write(f"  source_note\t{source_note}\n")
        for note in notes_by_label.get(label, []):
            locator = str(note.get("locator", ""))
            text = str(note.get("note", ""))
            sys.stdout.write(f"  note\t{locator}\t{text}\n")
    sys.stdout.write("# theorem_dependency_edges\n")
    for edge in graph["theorem_dependency_edges"]:
        if not isinstance(edge, dict):
            continue
        dependency = str(edge.get("dependency", ""))
        dependent = str(edge.get("dependent", ""))
        relation = str(edge.get("relation", "uses"))
        sys.stdout.write(f"{dependency}\t->\t{dependent}\t{relation}\n")


def _write_graph_mermaid(graph: dict) -> None:
    ids: dict[str, str] = {}
    nodes_by_label = {
        str(node.get("label", "")): node
        for node in graph["theorem_nodes"]
        if isinstance(node, dict) and node.get("label")
    }
    sys.stdout.write("graph TD\n")
    for label, node in nodes_by_label.items():
        sys.stdout.write(f'  {_mermaid_id(label, ids)}["{_mermaid_label(node)}"]\n')
    for edge in graph["theorem_dependency_edges"]:
        if not isinstance(edge, dict):
            continue
        dependency = str(edge.get("dependency", ""))
        dependent = str(edge.get("dependent", ""))
        if not dependency or not dependent:
            continue
        relation = str(edge.get("relation", "uses")).replace('"', "'")
        sys.stdout.write(
            f"  {_mermaid_id(dependency, ids)} -->|{relation}| {_mermaid_id(dependent, ids)}\n"
        )


def _write_graph_dot(graph: dict) -> None:
    sys.stdout.write("digraph review_graph {\n")
    sys.stdout.write("  rankdir=LR;\n")
    for node in graph["theorem_nodes"]:
        if not isinstance(node, dict):
            continue
        label = str(node.get("label", ""))
        if not label:
            continue
        kind = str(node.get("kind", ""))
        title = str(node.get("title", "") or node.get("statement", "") or label)
        node_label = "\n".join([label, kind, title])
        sys.stdout.write(f"  {_dot_quote(label)} [label={_dot_quote(node_label)}];\n")
    for edge in graph["theorem_dependency_edges"]:
        if not isinstance(edge, dict):
            continue
        dependency = str(edge.get("dependency", ""))
        dependent = str(edge.get("dependent", ""))
        if not dependency or not dependent:
            continue
        relation = str(edge.get("relation", "uses"))
        sys.stdout.write(
            f"  {_dot_quote(dependency)} -> {_dot_quote(dependent)} [label={_dot_quote(relation)}];\n"
        )
    sys.stdout.write("}\n")


def run_review(workspace: str | None, args: argparse.Namespace) -> int:
    ws = workspace_paths(workspace)
    ensure_initialised(ws)
    if args.review_command == "list":
        rows = list_reviews(ws.root)
        for row in rows:
            payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
            summary = payload.get("issue_summary", {}) if isinstance(payload.get("issue_summary"), dict) else {}
            sys.stdout.write(
                "\t".join(
                    [
                        str(payload.get("review_id", "") or row.get("event_id", "")),
                        str(row.get("target", "")),
                        str(payload.get("verdict", "")),
                        f"issues={summary.get('issue_count', 0)}",
                        f"blocks={bool(summary.get('blocks_acceptance', False))}",
                    ]
                )
                + "\n"
            )
        return 0

    if args.review_command == "show":
        row = _find_review(list_reviews(ws.root), args.review_id)
        if row is not None:
            sys.stdout.write(json.dumps(row, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
            return 0
        sys.stderr.write(f"review not found: {args.review_id}\n")
        return 1

    if args.review_command == "graph":
        row = _find_review(list_reviews(ws.root), args.review_id)
        if row is None:
            sys.stderr.write(f"review not found: {args.review_id}\n")
            return 1
        graph = _review_graph(_report_payload(row))
        output_format = getattr(args, "format", "text")
        if output_format == "json":
            sys.stdout.write(json.dumps(graph, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
        elif output_format == "mermaid":
            _write_graph_mermaid(graph)
        elif output_format == "dot":
            _write_graph_dot(graph)
        else:
            _write_graph_text(graph)
        return 0

    if args.review_command == "typos":
        row = _find_review(list_reviews(ws.root), args.review_id)
        if row is None:
            sys.stderr.write(f"review not found: {args.review_id}\n")
            return 1
        report = _report_payload(row)
        findings = report.get("typo_findings", [])
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict):
                continue
            typo_id = str(finding.get("typo_id", "") or finding.get("id", ""))
            severity = str(finding.get("severity", ""))
            locator = str(finding.get("locator", ""))
            observed = str(finding.get("observed", ""))
            suggested = str(finding.get("suggested", ""))
            reason = str(finding.get("reason", ""))
            sys.stdout.write(
                "\t".join([typo_id, severity, locator, observed, suggested, reason])
                + "\n"
            )
        return 0

    if args.review_command == "repair":
        return run_repair_command(ws.root, args)

    sys.stderr.write("review requires a subcommand: list, show, graph, typos, or repair\n")
    return 2


__all__ = ["run_review"]
