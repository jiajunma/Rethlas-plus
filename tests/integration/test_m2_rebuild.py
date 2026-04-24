"""M2 — rebuild_from_events contract + replay determinism."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from common.events.filenames import format_filename
from common.events.io import atomic_write_event
from common.kb.kuzu_backend import KuzuBackend
from librarian.rebuild import rebuild_from_events


def _write_event(
    events_root: Path,
    *,
    iso_ms: str,
    etype: str,
    target: str | None,
    actor: str,
    seq: int,
    uid: str,
    payload: dict[str, Any],
    ts: str = "2026-04-25T12:00:00.000+00:00",
) -> Path:
    date_dir = events_root / iso_ms[:8]  # YYYYMMDD
    date_dir.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "event_id": f"{iso_ms}-{seq:04d}-{uid}",
        "type": etype,
        "actor": actor,
        "ts": ts,
        "payload": payload,
    }
    if target is not None:
        body["target"] = target
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    name = format_filename(
        iso_ms=iso_ms,
        event_type=etype,
        target=target,
        actor=actor,
        seq=seq,
        uid=uid,
    )
    atomic_write_event(date_dir / name, raw)
    return date_dir / name


def _seed_three_events(events_root: Path) -> list[Path]:
    paths: list[Path] = []
    paths.append(
        _write_event(
            events_root,
            iso_ms="20260425T120000.000",
            etype="user.node_added",
            target="def:primary_object",
            actor="user:alice",
            seq=1,
            uid="abc0123456789abc",
            payload={
                "kind": "definition",
                "statement": "A primary object is X.",
                "proof": "",
                "remark": "",
                "source_note": "",
            },
        )
    )
    paths.append(
        _write_event(
            events_root,
            iso_ms="20260425T120001.000",
            etype="user.node_added",
            target="lem:foo",
            actor="user:alice",
            seq=1,
            uid="abc0123456789abd",
            payload={
                "kind": "lemma",
                "statement": r"About \ref{def:primary_object}.",
                "proof": "proof text",
                "remark": "",
                "source_note": "",
            },
        )
    )
    paths.append(
        _write_event(
            events_root,
            iso_ms="20260425T120002.000",
            etype="user.node_added",
            target="thm:bar",
            actor="user:alice",
            seq=1,
            uid="abc0123456789abe",
            payload={
                "kind": "theorem",
                "statement": r"About \ref{lem:foo}.",
                "proof": "proof text",
                "remark": "",
                "source_note": "",
            },
        )
    )
    return paths


def test_rebuild_produces_full_projection(tmp_path: Path) -> None:
    events = tmp_path / "events"
    _seed_three_events(events)

    backend = KuzuBackend(tmp_path / "dag.kz")
    try:
        trail = rebuild_from_events(backend=backend, events_root=events)
        assert [x[1] for x in trail] == ["applied"] * 3
        assert backend.node_labels() == [
            "def:primary_object",
            "lem:foo",
            "thm:bar",
        ]
    finally:
        backend.close()


def test_rebuild_wipes_stale_state(tmp_path: Path) -> None:
    events = tmp_path / "events"
    _seed_three_events(events)
    # Pre-populate the DB with a spurious node. rebuild must clear it.
    backend = KuzuBackend(tmp_path / "dag.kz")
    try:
        from common.kb.types import Node, NodeKind

        backend.create_node(
            Node(
                label="ghost:stale",
                kind=NodeKind.LEMMA,
                statement="should disappear",
                proof="",
                remark="",
                source_note="",
                pass_count=0,
                repair_count=0,
                statement_hash="x",
                verification_hash="y",
                depends_on=(),
            )
        )
        rebuild_from_events(backend=backend, events_root=events)
        labels = backend.node_labels()
        assert "ghost:stale" not in labels
    finally:
        backend.close()


def test_rebuild_order_independent_of_filesystem_iter(tmp_path: Path) -> None:
    """Scramble the filesystem mtime order; rebuild still honours
    (iso_ms, seq, uid) ordering."""
    events = tmp_path / "events"
    paths = _seed_three_events(events)

    # Touch the files in reverse order to jumble filesystem-iteration
    # (mtime) ordering.
    import time

    for p in reversed(paths):
        p.touch()
        time.sleep(0.001)

    backend = KuzuBackend(tmp_path / "dag.kz")
    try:
        trail = rebuild_from_events(backend=backend, events_root=events)
        ordered_paths = [t[0] for t in trail]
        # Must match the original iso_ms order, not the touch order.
        assert ordered_paths == paths
    finally:
        backend.close()


def test_two_rebuilds_produce_same_labels(tmp_path: Path) -> None:
    events = tmp_path / "events"
    _seed_three_events(events)
    backend = KuzuBackend(tmp_path / "dag.kz")
    try:
        rebuild_from_events(backend=backend, events_root=events)
        labels1 = backend.node_labels()
        # capture hashes
        hashes1 = {lbl: backend.node_by_label(lbl).statement_hash for lbl in labels1}

        rebuild_from_events(backend=backend, events_root=events)
        labels2 = backend.node_labels()
        hashes2 = {lbl: backend.node_by_label(lbl).statement_hash for lbl in labels2}

        assert labels1 == labels2
        assert hashes1 == hashes2
    finally:
        backend.close()


def test_applied_event_sha256_matches_file_bytes(tmp_path: Path) -> None:
    """AppliedEvent.event_sha256 equals sha256 of the raw on-disk bytes."""
    import hashlib

    events = tmp_path / "events"
    paths = _seed_three_events(events)

    backend = KuzuBackend(tmp_path / "dag.kz")
    try:
        rebuild_from_events(backend=backend, events_root=events)
        for p in paths:
            expected = hashlib.sha256(p.read_bytes()).hexdigest()
            # Read event_id from file to look it up in AppliedEvent.
            body = json.loads(p.read_text(encoding="utf-8"))
            row = backend.applied_event(body["event_id"])
            assert row is not None
            assert row.event_sha256 == expected
    finally:
        backend.close()


def test_merkle_cascade_updates_dependents(tmp_path: Path) -> None:
    """Revising a dep's statement cascades to dependents."""
    events = tmp_path / "events"
    _seed_three_events(events)

    backend = KuzuBackend(tmp_path / "dag.kz")
    try:
        rebuild_from_events(backend=backend, events_root=events)
        before_lem = backend.node_by_label("lem:foo").statement_hash
        before_thm = backend.node_by_label("thm:bar").statement_hash

        # Revise def:primary_object's statement. This changes its
        # statement_hash -> lem:foo cascades -> thm:bar cascades.
        _write_event(
            events,
            iso_ms="20260425T120010.000",
            etype="user.node_revised",
            target="def:primary_object",
            actor="user:alice",
            seq=1,
            uid="abc0123456789aff",
            payload={
                "kind": "definition",
                "statement": "A primary object is XYZ (revised).",
                "proof": "",
                "remark": "",
                "source_note": "",
            },
        )
        rebuild_from_events(backend=backend, events_root=events)
        after_lem = backend.node_by_label("lem:foo").statement_hash
        after_thm = backend.node_by_label("thm:bar").statement_hash
        assert after_lem != before_lem, "lem:foo must pick up new dep statement_hash"
        assert after_thm != before_thm, "thm:bar must cascade further up"
    finally:
        backend.close()
