"""Contract tests for the Kuzu-free MarkdownBackend (§13).

Mirror the behaviours the projector/daemon rely on: node CRUD, dependency
graph + cycle detection, the AppliedEvent ledger, snapshot transactions, and
the markdown-on-commit persistence of *all* nodes (staged and verified).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.kb.markdown_backend import MarkdownBackend
from common.kb.types import AppliedEvent, ApplyOutcome, Node, NodeKind


def _node(label="lem:foo", *, kind=NodeKind.LEMMA, pass_count=0, depends_on=(), **kw) -> Node:
    base = dict(
        label=label,
        kind=kind,
        statement="A statement.",
        proof="A proof.",
        remark="",
        source_note="",
        pass_count=pass_count,
        repair_count=0,
        statement_hash="a" * 64,
        verification_hash="b" * 64,
        depends_on=depends_on,
    )
    base.update(kw)
    return Node(**base)


def test_create_read_and_commit_writes_markdown(tmp_path: Path) -> None:
    nodes_dir = tmp_path / "nodes"
    kb = MarkdownBackend(nodes_dir)
    kb.begin()
    kb.create_node(_node("lem:foo", pass_count=0))      # staged
    kb.create_node(_node("def:x", kind=NodeKind.DEFINITION, proof="", pass_count=3))  # verified
    kb.commit()

    row = kb.node_by_label("lem:foo")
    assert row is not None and row.kind == "lemma" and row.pass_count == 0
    assert kb.node_labels() == ["def:x", "lem:foo"]
    # All nodes — staged AND verified — are on disk as markdown.
    assert (nodes_dir / "lem_foo.md").exists()
    assert (nodes_dir / "def_x.md").exists()


def test_reload_from_markdown_recovers_math(tmp_path: Path) -> None:
    nodes_dir = tmp_path / "nodes"
    kb = MarkdownBackend(nodes_dir)
    kb.begin()
    kb.create_node(_node("lem:foo", pass_count=2))
    kb.commit()

    fresh = MarkdownBackend(nodes_dir)  # cold start reads markdown
    row = fresh.node_by_label("lem:foo")
    assert row is not None and row.pass_count == 2 and row.statement == "A statement."


def test_rollback_discards_writes(tmp_path: Path) -> None:
    nodes_dir = tmp_path / "nodes"
    kb = MarkdownBackend(nodes_dir)
    kb.begin()
    kb.create_node(_node("lem:foo"))
    kb.rollback()
    assert kb.node_by_label("lem:foo") is None
    assert not (nodes_dir / "lem_foo.md").exists()


def test_set_node_fields_math_and_operational(tmp_path: Path) -> None:
    kb = MarkdownBackend(tmp_path / "nodes")
    kb.begin()
    kb.create_node(_node("lem:foo", pass_count=0))
    kb.commit()
    kb.begin()
    kb.set_node_fields("lem:foo", pass_count=1, repair_count=2, repair_hint="fix it")
    kb.commit()
    row = kb.node_by_label("lem:foo")
    assert row.pass_count == 1            # math (also in markdown)
    assert row.repair_count == 2          # operational (in-memory)
    assert row.repair_hint == "fix it"


def test_dependencies_dependents_and_cycle(tmp_path: Path) -> None:
    kb = MarkdownBackend(tmp_path / "nodes")
    kb.begin()
    kb.create_node(_node("def:base", kind=NodeKind.DEFINITION, proof="", pass_count=3))
    kb.create_node(_node("lem:foo", depends_on=("def:base",)))
    kb.commit()
    assert kb.dependencies_of("lem:foo") == ["def:base"]
    assert kb.dependents_of("def:base") == ["lem:foo"]
    # Adding def:base -> lem:foo would close def:base -> lem:foo -> def:base.
    cycle = kb.would_introduce_cycle("def:base", ["lem:foo"])
    assert cycle is not None and cycle[0] == "def:base"
    assert kb.would_introduce_cycle("lem:foo", ["def:base"]) is None  # already a dep, no cycle


def test_applied_event_ledger(tmp_path: Path) -> None:
    kb = MarkdownBackend(tmp_path / "nodes")
    assert kb.applied_event("e1") is None
    kb.begin()
    kb.record_applied_event(
        event_id="e1", status=ApplyOutcome.APPLIED, event_sha256="s" * 64,
        target_label="lem:foo", applied_at="2026-06-02T00:00:00.000Z",
    )
    kb.commit()
    ev = kb.applied_event("e1")
    assert isinstance(ev, AppliedEvent) and ev.is_applied
    assert kb.applied_event_counts() == (1, 1, 0)
    assert kb.last_applied_event_id() == "e1"


def test_wipe_clears_memory_and_markdown(tmp_path: Path) -> None:
    nodes_dir = tmp_path / "nodes"
    kb = MarkdownBackend(nodes_dir)
    kb.begin()
    kb.create_node(_node("lem:foo"))
    kb.commit()
    kb.wipe()
    assert kb.node_labels() == []
    assert list(nodes_dir.glob("*.md")) == []
