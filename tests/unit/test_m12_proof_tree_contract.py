"""M12 — proof-tree backend and template contracts."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from common.kb.types import NodeKind
from dashboard import state as dashboard_state
from dashboard.kb_client import NodeRow
from dashboard.server import DashboardCore
from dashboard.templates import INDEX_HTML


def _row(
    label: str,
    kind: str,
    *,
    statement: str = "S.",
    proof: str = "P.",
    pass_count: int = 0,
    deps: tuple[str, ...] = (),
) -> NodeRow:
    return NodeRow(
        label=label,
        kind=kind,
        statement=statement,
        proof=proof,
        pass_count=pass_count,
        repair_count=0,
        statement_hash="sh",
        verification_hash="vh",
        repair_hint="",
        verification_report="",
        deps=deps,
        introduced_by_actor="user:test",
    )


def test_tree_empty_kb(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("dashboard.server.list_nodes", lambda _ws: [])
    monkeypatch.setattr("dashboard.server.list_jobs", lambda _jobs: [])

    tree = DashboardCore(tmp_path).tree()

    assert tree["trees"] == []
    assert tree["node_count"] == 0
    assert tree["edge_count"] == 0


def test_tree_single_theorem_no_deps(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "dashboard.server.list_nodes",
        lambda _ws: [_row("thm:t", "theorem")],
    )
    monkeypatch.setattr("dashboard.server.list_jobs", lambda _jobs: [])

    tree = DashboardCore(tmp_path).tree()

    assert tree["node_count"] == 1
    assert tree["edge_count"] == 0
    root = tree["trees"][0]
    assert root["label"] == "thm:t"
    assert root["kind"] == "theorem"
    assert root["children"] == []


def test_tree_marks_phase2_attention_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "dashboard.server.list_nodes",
        lambda _ws: [
            _row(
                "thm:t",
                "theorem",
                pass_count=-1,
                deps=("lem:bg",),
            ),
            _row(
                "lem:bg",
                "lemma",
                pass_count=-1,
                deps=(),
            ),
        ],
    )
    monkeypatch.setattr("dashboard.server.list_jobs", lambda _jobs: [])
    (tmp_path / "runtime" / "state").mkdir(parents=True)
    (tmp_path / "runtime" / "state" / "coordinator.json").write_text(
        """{
          "attention_targets": [
            {
              "kind": "generic_background_stuck",
              "target": "lem:bg",
              "trigger": "generic_background_expansion",
              "reason": "generator_background_helper_rejected",
              "count": 1,
              "message": "generic background helper stuck on lem:bg"
            }
          ]
        }""",
        encoding="utf-8",
    )

    tree = DashboardCore(tmp_path).tree("thm:t")

    child = tree["trees"][0]["children"][0]
    assert child["status"] == "generic_background_stuck"
    assert child["attention"]["trigger"] == "generic_background_expansion"


def test_tree_attention_does_not_override_done_or_in_flight(monkeypatch, tmp_path) -> None:
    class _Job:
        target = "lem:running"
        status = "running"

    monkeypatch.setattr(
        "dashboard.server.list_nodes",
        lambda _ws: [
            _row(
                "thm:t",
                "theorem",
                pass_count=0,
                deps=("lem:done", "lem:running"),
            ),
            _row(
                "lem:done",
                "lemma",
                pass_count=3,
                deps=(),
            ),
            _row(
                "lem:running",
                "lemma",
                pass_count=-1,
                deps=(),
            ),
        ],
    )
    monkeypatch.setattr("dashboard.server.list_jobs", lambda _jobs: [_Job()])
    (tmp_path / "runtime" / "state").mkdir(parents=True)
    (tmp_path / "runtime" / "state" / "coordinator.json").write_text(
        """{
          "attention_targets": [
            {
              "kind": "search_branch_stuck",
              "target": "lem:done",
              "trigger": "repair_budget_exhausted",
              "reason": "max_automatic_repairs",
              "count": 3,
              "message": "stale stuck entry for done node"
            },
            {
              "kind": "search_branch_stuck",
              "target": "lem:running",
              "trigger": "repair_budget_exhausted",
              "reason": "max_automatic_repairs",
              "count": 3,
              "message": "stale stuck entry for running node"
            }
          ]
        }""",
        encoding="utf-8",
    )

    tree = DashboardCore(tmp_path).tree("thm:t")

    children = {child["label"]: child for child in tree["trees"][0]["children"]}
    assert children["lem:done"]["status"] == "done"
    assert children["lem:running"]["status"] == "in_flight"


def test_tree_cycle_defense_marks_second_occurrence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "dashboard.server.list_nodes",
        lambda _ws: [
            _row("thm:t", "theorem", deps=("lem:a",)),
            _row("lem:a", "lemma", deps=("thm:t",)),
        ],
    )
    monkeypatch.setattr("dashboard.server.list_jobs", lambda _jobs: [])

    tree = DashboardCore(tmp_path).tree("thm:t")

    second_thm = tree["trees"][0]["children"][0]["children"][0]
    assert second_thm["label"] == "thm:t"
    assert second_thm["cycle_detected"] is True
    assert second_thm["children"] == []


def test_template_contains_proof_tree_at_documented_position() -> None:
    kb = INDEX_HTML.index('<div class="grid" id="kb"></div>')
    proof_tree = INDEX_HTML.index('id="proof_tree"')
    active = INDEX_HTML.index('id="active"')

    assert kb < proof_tree < active
    assert "loadProofTree()" in INDEX_HTML
    assert "renderTreeNode(" in INDEX_HTML
    assert "expandToDepth(" in INDEX_HTML
    assert "subscribeProofTree(" in INDEX_HTML
    assert 'addEventListener("coordinator_tick"' in INDEX_HTML


def test_template_css_covers_all_kinds_and_statuses() -> None:
    for kind in (k.value for k in NodeKind):
        assert f'[data-node-kind="{kind}"]' in INDEX_HTML

    statuses = [
        value
        for name, value in vars(dashboard_state).items()
        if name.startswith("STATUS_") and isinstance(value, str)
    ]
    for status in statuses:
        assert f'[data-node-status="{status}"]' in INDEX_HTML

    assert '[data-node-status="missing_from_nodes"]' in INDEX_HTML


def test_template_defines_visual_tokens_and_badge_classes() -> None:
    required = [
        "--pt-kind-definition:",
        "--pt-kind-proposition:",
        "--pt-kind-lemma:",
        "--pt-kind-theorem:",
        "--pt-kind-external-theorem:",
        "--pt-status-done-bg:",
        "--pt-status-verified-bg:",
        "--pt-status-needs-verification-bg:",
        "--pt-status-blocked-on-dependency-bg:",
        "--pt-status-needs-generation-bg:",
        "--pt-status-generation-blocked-on-dependency-bg:",
        "--pt-status-user-blocked-bg:",
        "--pt-status-in-flight-bg:",
        "--pt-status-search-branch-stuck-bg:",
        "--pt-status-generic-background-stuck-bg:",
        ".pass-badge",
        ".repair-badge",
        ".in-flight-badge",
        ".shared-parents-chip",
    ]
    for token in required:
        assert token in INDEX_HTML


def test_dashboard_body_script_is_valid_javascript(tmp_path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    m = re.search(r"<body>.*?<script>(?P<script>.*)</script>\s*</body>", INDEX_HTML, re.S)
    assert m is not None
    js = tmp_path / "dashboard-body.js"
    js.write_text(m.group("script"), encoding="utf-8")

    result = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
