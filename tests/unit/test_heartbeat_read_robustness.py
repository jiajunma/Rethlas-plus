"""Heartbeat readers must tolerate any filesystem error.

Heartbeats are observability state (§6.5 / §6.4.2 / §6.7.1). A
transient ``PermissionError`` or ``IsADirectoryError`` while reading
``runtime/state/{coordinator,librarian,dashboard}.json`` must NOT crash
consumers — coordinator dispatch loop, dashboard supervisor, dashboard
HTTP layer, and the linter all rely on these readers being graceful.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from coordinator import heartbeat as coord_hb
from dashboard import heartbeat as dash_hb
from librarian import heartbeat as lib_hb


def test_librarian_read_heartbeat_returns_none_on_oserror(tmp_path: Path) -> None:
    path = tmp_path / "librarian.json"
    path.write_text("{}", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        assert lib_hb.read_heartbeat(path) is None


def test_librarian_read_heartbeat_returns_none_on_missing(tmp_path: Path) -> None:
    assert lib_hb.read_heartbeat(tmp_path / "missing.json") is None


def test_librarian_read_heartbeat_returns_none_on_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "librarian.json"
    path.write_text("{not json", encoding="utf-8")
    assert lib_hb.read_heartbeat(path) is None


def test_coordinator_read_heartbeat_returns_none_on_oserror(tmp_path: Path) -> None:
    path = tmp_path / "coordinator.json"
    path.write_text("{}", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        assert coord_hb.read_heartbeat(path) is None


def test_dashboard_read_heartbeat_returns_none_on_oserror(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.json"
    path.write_text("{}", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        assert dash_hb.read_heartbeat(path) is None


def test_dashboard_read_heartbeat_returns_none_when_path_is_directory(tmp_path: Path) -> None:
    """Reading a directory raises IsADirectoryError, a subclass of OSError."""
    dir_as_path = tmp_path / "looks_like_a_file.json"
    dir_as_path.mkdir()
    assert dash_hb.read_heartbeat(dir_as_path) is None
