"""M2 — Kuzu backend: schema init, transactional apply, idempotent replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from common.kb.markdown_backend import MarkdownBackend


def _backend(tmp_path: Path) -> MarkdownBackend:
    return MarkdownBackend(tmp_path / "nodes")






def test_applied_event_round_trip(tmp_path: Path) -> None:
    be = _backend(tmp_path)
    try:
        from common.kb.types import ApplyOutcome

        assert be.applied_event("nope") is None
        row = be.record_applied_event(
            event_id="20260425T120000.000-0001-a7b2c912d4f1e380",
            status=ApplyOutcome.APPLIED,
            event_sha256="deadbeef" * 8,
            target_label="def:x",
        )
        assert row.event_id.startswith("20260425T120000.000")
        assert row.is_applied
        fetched = be.applied_event(row.event_id)
        assert fetched is not None
        assert fetched.status is ApplyOutcome.APPLIED
        assert fetched.target_label == "def:x"
    finally:
        be.close()


def test_cycle_detection_helper(tmp_path: Path) -> None:
    be = _backend(tmp_path)
    try:
        from common.kb.types import Node, NodeKind

        def _mk(label: str, deps: tuple[str, ...] = ()) -> Node:
            return Node(
                label=label,
                kind=NodeKind.LEMMA,
                statement=label,
                proof="",
                remark="",
                source_note="",
                pass_count=-1,
                repair_count=0,
                statement_hash="sh-" + label,
                verification_hash="vh-" + label,
                depends_on=deps,
            )

        be.create_node(_mk("lem:a"))
        be.create_node(_mk("lem:b", ("lem:a",)))
        # Adding lem:a depends-on lem:b would close a cycle.
        cycle = be.would_introduce_cycle("lem:a", ["lem:b"])
        assert cycle is not None
        assert cycle[0] == "lem:a" and cycle[-1] == "lem:a"
        # Adding a depends-on a brand new node is safe.
        assert be.would_introduce_cycle("lem:a", ["lem:c"]) is None
    finally:
        be.close()
