"""M9/§6.4 — librarian restart-once-then-fatal policy in coordinator."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from coordinator.main import (
    _LIBRARIAN_RECOVERY_WINDOW_S,
    _LibrarianFatal,
    _recover_librarian_if_needed,
)


class _FakeLibrarian:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


@dataclass
class _FakeWS:
    root: Path
    runtime_logs: Path


@dataclass
class _FakeState:
    librarian: Any
    ws: _FakeWS
    last_librarian_restart_monotonic: float = 0.0


def _state(tmp_path: Path, alive: bool) -> _FakeState:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return _FakeState(
        librarian=_FakeLibrarian(alive=alive),
        ws=_FakeWS(root=tmp_path, runtime_logs=logs),
    )


def test_recover_no_op_when_alive(tmp_path: Path) -> None:
    state = _state(tmp_path, alive=True)
    # Should not raise and should not call spawn_librarian.
    with patch("coordinator.main.spawn_librarian") as spawn:
        _recover_librarian_if_needed(state)
    assert spawn.call_count == 0


def test_recover_restarts_once_when_dead_and_no_prior_restart(tmp_path: Path) -> None:
    state = _state(tmp_path, alive=False)
    new_lib = _FakeLibrarian(alive=True)
    with patch("coordinator.main.spawn_librarian", return_value=new_lib) as spawn, \
         patch("coordinator.main._wait_for_librarian_ready", return_value=True):
        _recover_librarian_if_needed(state)
    assert spawn.call_count == 1
    assert state.librarian is new_lib
    assert state.last_librarian_restart_monotonic > 0


def test_recover_fatal_when_second_crash_inside_window(tmp_path: Path) -> None:
    state = _state(tmp_path, alive=False)
    state.last_librarian_restart_monotonic = time.monotonic() - 30  # 30 s ago
    with patch("coordinator.main.spawn_librarian") as spawn:
        with pytest.raises(_LibrarianFatal):
            _recover_librarian_if_needed(state)
    assert spawn.call_count == 0


def test_recover_restarts_when_second_crash_after_window(tmp_path: Path) -> None:
    state = _state(tmp_path, alive=False)
    state.last_librarian_restart_monotonic = (
        time.monotonic() - (_LIBRARIAN_RECOVERY_WINDOW_S + 1)
    )
    new_lib = _FakeLibrarian(alive=True)
    with patch("coordinator.main.spawn_librarian", return_value=new_lib), \
         patch("coordinator.main._wait_for_librarian_ready", return_value=True):
        _recover_librarian_if_needed(state)
    assert state.librarian is new_lib


def test_recover_fatal_when_restart_fails_to_reach_ready(tmp_path: Path) -> None:
    state = _state(tmp_path, alive=False)
    new_lib = _FakeLibrarian(alive=True)
    with patch("coordinator.main.spawn_librarian", return_value=new_lib), \
         patch("coordinator.main._wait_for_librarian_ready", return_value=False):
        with pytest.raises(_LibrarianFatal):
            _recover_librarian_if_needed(state)
