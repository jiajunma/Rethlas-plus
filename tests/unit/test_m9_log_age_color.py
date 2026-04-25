"""M9 — Codex log-age color grading (ARCHITECTURE §6.7).

Thresholds:
- green:  age <= 5 min
- yellow: 5 min < age <= min(T/2, 15 min)
- orange: min(T/2, 15 min) < age < T
- red:    age >= T
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dashboard.server import _log_age_color, _log_age_seconds


def test_green_under_5_minutes() -> None:
    assert _log_age_color(0.0, timeout_s=1800.0) == "green"
    assert _log_age_color(299.0, timeout_s=1800.0) == "green"
    assert _log_age_color(300.0, timeout_s=1800.0) == "green"


def test_yellow_between_5_and_15_minutes_for_default_timeout() -> None:
    # T = 1800; min(T/2, 15min) = 900
    assert _log_age_color(301.0, timeout_s=1800.0) == "yellow"
    assert _log_age_color(900.0, timeout_s=1800.0) == "yellow"


def test_orange_between_yellow_cap_and_T() -> None:
    assert _log_age_color(901.0, timeout_s=1800.0) == "orange"
    assert _log_age_color(1799.0, timeout_s=1800.0) == "orange"


def test_red_at_or_above_T() -> None:
    assert _log_age_color(1800.0, timeout_s=1800.0) == "red"
    assert _log_age_color(3600.0, timeout_s=1800.0) == "red"


def test_short_timeout_caps_yellow_at_T_over_two() -> None:
    # T = 600; min(T/2, 15min) = 300; so green window collapses into yellow
    # immediately after 300s. With T/2 == 300 yellow upper bound is also 300.
    assert _log_age_color(300.0, timeout_s=600.0) == "green"
    assert _log_age_color(301.0, timeout_s=600.0) == "orange"


def test_unknown_for_missing_file() -> None:
    assert _log_age_color(None, timeout_s=1800.0) == "unknown"


def test_log_age_seconds_resolves_relative_path_against_ws_root(tmp_path: Path) -> None:
    """§6.7.1 stores log_path relative to workspace root; the dashboard's
    CWD is not the workspace, so the resolver must use ws_root or
    os.stat will always fail and the §6.7 color grading collapses to
    "unknown" on every job."""
    log = tmp_path / "runtime" / "logs" / "ver-test.codex.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("hello", encoding="utf-8")
    # Set mtime to ~10s ago.
    os.utime(log, (time.time() - 10, time.time() - 10))

    age = _log_age_seconds("runtime/logs/ver-test.codex.log", ws_root=tmp_path)
    assert age is not None
    assert 9.0 <= age <= 30.0


def test_log_age_seconds_returns_none_when_relative_unresolvable(tmp_path: Path) -> None:
    # Without ws_root, a relative path stat-fails (CWD mismatch) and we
    # return None — better than misreporting a stale age.
    age = _log_age_seconds("runtime/logs/nope.codex.log", ws_root=tmp_path)
    assert age is None
    age = _log_age_seconds("")
    assert age is None


def test_log_age_seconds_accepts_absolute_path(tmp_path: Path) -> None:
    log = tmp_path / "absolute.log"
    log.write_text("x", encoding="utf-8")
    os.utime(log, (time.time() - 5, time.time() - 5))
    age = _log_age_seconds(str(log))
    assert age is not None
    assert 4.0 <= age <= 30.0
