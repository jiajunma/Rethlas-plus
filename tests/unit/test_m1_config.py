"""M1 — ``rethlas.toml`` parsing + validation (§2.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from common.config import (
    ConfigError,
    DashboardConfig,
    RethlasConfig,
    SchedulingConfig,
    load_config,
)


def _write(tmp: Path, body: str) -> Path:
    p = tmp / "rethlas.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "rethlas.toml")
    assert cfg == RethlasConfig()
    assert cfg.scheduling == SchedulingConfig()
    assert cfg.dashboard == DashboardConfig()


def test_none_path_returns_defaults() -> None:
    assert load_config(None) == RethlasConfig()


def test_full_valid_file_parses(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        """
[scheduling]
desired_pass_count = 5
generator_workers = 3
verifier_workers = 6
codex_silent_timeout_seconds = 600

[dashboard]
bind = "0.0.0.0:9000"
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.scheduling == SchedulingConfig(
        desired_pass_count=5,
        generator_workers=3,
        verifier_workers=6,
        codex_silent_timeout_seconds=600,
    )
    assert cfg.dashboard == DashboardConfig(bind="0.0.0.0:9000")


def test_partial_file_merges_with_defaults(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path,
        """
[scheduling]
generator_workers = 1
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.scheduling.generator_workers == 1
    # untouched fields keep defaults
    assert cfg.scheduling.desired_pass_count == 3
    assert cfg.scheduling.verifier_workers == 4
    assert cfg.scheduling.codex_silent_timeout_seconds == 1800
    assert cfg.dashboard.bind == "127.0.0.1:8765"


@pytest.mark.parametrize(
    "body, bad_field",
    [
        ("[scheduling]\ndesired_pass_count = 0\n", "desired_pass_count"),
        ("[scheduling]\ngenerator_workers = -1\n", "generator_workers"),
        ("[scheduling]\nverifier_workers = 0\n", "verifier_workers"),
        ("[scheduling]\ncodex_silent_timeout_seconds = 30\n", "codex_silent_timeout_seconds"),
    ],
)
def test_out_of_range_scheduling_rejected(tmp_path: Path, body: str, bad_field: str) -> None:
    cfg_path = _write(tmp_path, body)
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    assert bad_field in str(exc.value)


@pytest.mark.parametrize(
    "bind",
    [
        "127.0.0.1",  # missing port
        "0.0.0.0:70000",  # port out of range
        ":8765",  # empty host
        "host:0",  # port below 1
        "host:-1",
        "no-colon-here",
    ],
)
def test_bad_bind_rejected(tmp_path: Path, bind: str) -> None:
    cfg_path = _write(tmp_path, f'[dashboard]\nbind = "{bind}"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    assert "bind" in str(exc.value)


def test_unknown_field_logged_and_ignored(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg_path = _write(
        tmp_path,
        """
[scheduling]
bogus_key = 42
desired_pass_count = 2
""",
    )
    with caplog.at_level("WARNING", logger="common.config.loader"):
        cfg = load_config(cfg_path)
    assert cfg.scheduling.desired_pass_count == 2
    assert any("bogus_key" in rec.message for rec in caplog.records)


def test_unknown_section_logged_and_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg_path = _write(
        tmp_path,
        """
[bogus]
anything = "here"

[scheduling]
desired_pass_count = 4
""",
    )
    with caplog.at_level("WARNING", logger="common.config.loader"):
        cfg = load_config(cfg_path)
    assert cfg.scheduling.desired_pass_count == 4
    assert any("bogus" in rec.message for rec in caplog.records)


def test_malformed_toml_raises(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, "[scheduling\n")  # unterminated section
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    # malformed TOML should carry enough context for the user to locate it
    assert "rethlas.toml" in str(exc.value) or "malformed" in str(exc.value).lower()


def test_scheduling_field_type_error(tmp_path: Path) -> None:
    cfg_path = _write(tmp_path, '[scheduling]\ngenerator_workers = "two"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    assert "generator_workers" in str(exc.value)
