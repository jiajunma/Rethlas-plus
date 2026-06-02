from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common.phase3.artifacts import write_json_atomic
from tests.fixtures.tmp_workspace import make_workspace


PYTHON = sys.executable


def test_review_list_and_show(tmp_path: Path) -> None:
    make_workspace(tmp_path, seed_config=True)
    write_json_atomic(
        tmp_path / "reviews" / "evt.json",
        {
            "schema": "rethlas-referee-artifact-v1",
            "event_id": "evt",
            "event_type": "referee.review_completed",
            "target": "thm:toy",
            "payload": {
                "review_id": "review_toy_001",
                "verdict": "needs_revision",
                "issue_summary": {"issue_count": 1, "blocks_acceptance": True},
                "report": {
                    "theorem_nodes": [
                        {
                            "label": "thm:toy_main",
                            "kind": "theorem",
                            "title": "主定理",
                            "status": "conditional",
                            "extraction_kind": "explicit_environment",
                        },
                        {
                            "label": "rem:toy_warning",
                            "kind": "remark",
                            "title": "关于记号的说明",
                            "status": "context",
                            "extraction_kind": "explicit_environment",
                            "source_locator": "PDF p. 11, Remark 1.2",
                            "source_note": "remark is a graph node",
                        },
                        {
                            "label": "def:toy_implicit",
                            "kind": "definition",
                            "title": "自然段中的隐式定义",
                            "status": "source_claim",
                            "extraction_kind": "implicit_paragraph",
                            "source_locator": "PDF p. 12, paragraph after (1.3)",
                            "source_note": "paragraph functions as a definition",
                        }
                    ],
                    "theorem_dependency_edges": [
                        {
                            "dependency": "assump:toy_input",
                            "dependent": "thm:toy_main",
                            "relation": "assumes",
                        }
                    ],
                    "node_location_notes": [
                        {
                            "label": "thm:toy_main",
                            "locator": "PDF p. 10",
                            "note": "定理 1.1。",
                        }
                    ],
                    "typo_findings": [
                        {
                            "typo_id": "typo:toy:p10",
                            "severity": "major",
                            "locator": "PDF p. 10",
                            "observed": "x",
                            "suggested": "y",
                            "reason": "notation drift",
                        }
                    ],
                },
            },
        },
    )
    listed = subprocess.run(
        [PYTHON, "-m", "cli.main", "--workspace", str(tmp_path), "review", "list"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert "review_toy_001" in listed.stdout
    assert "blocks=True" in listed.stdout

    shown = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "show",
            "review_toy_001",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert '"review_id": "review_toy_001"' in shown.stdout

    graph = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "graph",
            "review_toy_001",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert graph.returncode == 0, graph.stderr
    assert "thm:toy_main" in graph.stdout
    assert "rem:toy_warning\tremark" in graph.stdout
    assert "def:toy_implicit\tdefinition\tsource_claim\timplicit_paragraph" in graph.stdout
    assert "paragraph functions as a definition" in graph.stdout
    assert "assump:toy_input\t->\tthm:toy_main\tassumes" in graph.stdout
    assert "PDF p. 10" in graph.stdout

    graph_json = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "graph",
            "review_toy_001",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert graph_json.returncode == 0, graph_json.stderr
    assert '"theorem_nodes"' in graph_json.stdout
    assert '"def:toy_implicit"' in graph_json.stdout

    graph_mermaid = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "graph",
            "review_toy_001",
            "--format",
            "mermaid",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert graph_mermaid.returncode == 0, graph_mermaid.stderr
    assert "graph TD" in graph_mermaid.stdout
    assert "-->|assumes|" in graph_mermaid.stdout

    graph_dot = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "graph",
            "review_toy_001",
            "--format",
            "dot",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert graph_dot.returncode == 0, graph_dot.stderr
    assert "digraph review_graph" in graph_dot.stdout
    assert '"assump:toy_input" -> "thm:toy_main"' in graph_dot.stdout

    typos = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "typos",
            "review_toy_001",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert typos.returncode == 0, typos.stderr
    assert "typo:toy:p10" in typos.stdout
    assert "notation drift" in typos.stdout

    repair = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "repair",
            "run",
            "review_toy_001",
            "--label",
            "thm:toy_main",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repair.returncode == 0, repair.stderr
    assert '"node_count": 1' in repair.stdout
    assert (tmp_path / "reviews" / "_repairs").is_dir()

    repair_again = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "repair",
            "run",
            "review_toy_001",
            "--label",
            "thm:toy_main",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repair_again.returncode == 0, repair_again.stderr
    assert '"node_count": 1' in repair_again.stdout

    repairs = subprocess.run(
        [
            PYTHON,
            "-m",
            "cli.main",
            "--workspace",
            str(tmp_path),
            "review",
            "repair",
            "list",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repairs.returncode == 0, repairs.stderr
    assert "review_toy_001" in repairs.stdout
    assert "version=2" in repairs.stdout
    assert "active=true" in repairs.stdout
    assert "active=false" in repairs.stdout
