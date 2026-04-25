"""M9 — Codex log-age color grading (ARCHITECTURE §6.7).

Thresholds:
- green:  age <= 5 min
- yellow: 5 min < age <= min(T/2, 15 min)
- orange: min(T/2, 15 min) < age < T
- red:    age >= T
"""

from __future__ import annotations

from dashboard.server import _log_age_color


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
