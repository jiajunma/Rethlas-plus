"""M1 — smoke test for the shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tests.fixtures.event_bytes import write_event_with_bytes
from tests.fixtures.fake_clock import FakeClock, counter_rng
from tests.fixtures.inject import inject_event_file
from tests.fixtures.tmp_workspace import make_workspace


def test_make_workspace_creates_full_skeleton(tmp_path: Path) -> None:
    root = make_workspace(tmp_path / "ws")
    for rel in [
        "events",
        "knowledge_base",
        "knowledge_base/nodes",
        "runtime/jobs",
        "runtime/logs",
        "runtime/locks",
        "runtime/state",
    ]:
        assert (root / rel).is_dir(), f"{rel} missing"


def test_make_workspace_seed_config(tmp_path: Path) -> None:
    root = make_workspace(tmp_path / "ws", seed_config=True)
    body = (root / "rethlas.toml").read_text(encoding="utf-8")
    assert "desired_pass_count" in body
    assert "bind" in body


def test_fake_clock_step_semantics() -> None:
    start = datetime(2026, 4, 25, tzinfo=timezone.utc)
    c = FakeClock(start)
    assert c() == start
    c.advance(milliseconds=10)
    assert (c() - start).total_seconds() * 1000 == 10
    c.step_back(milliseconds=4)
    assert (c() - start).total_seconds() * 1000 == 6


def test_counter_rng_deterministic() -> None:
    rng = counter_rng(start=0)
    a = rng(4)
    b = rng(4)
    assert a != b
    assert a == (1).to_bytes(4, "big")
    assert b == (2).to_bytes(4, "big")


def test_write_event_with_bytes(tmp_path: Path) -> None:
    p = write_event_with_bytes(tmp_path / "events" / "2026-04-25" / "x.json", b"HELLO")
    assert p.read_bytes() == b"HELLO"


def test_inject_event_file(tmp_path: Path) -> None:
    events_dir = tmp_path / "events" / "2026-04-25"
    f = inject_event_file(events_dir, "x.json", b"body")
    assert f.read_bytes() == b"body"
