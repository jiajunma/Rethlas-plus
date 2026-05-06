"""SchedulingConfig.use_voi_scoring — Phase B rollback toggle parsing."""

from __future__ import annotations

import pytest

from common.config.loader import (
    ConfigError,
    DEFAULT_USE_VOI_SCORING,
    SchedulingConfig,
    load_config,
)


def _write_toml(tmp_path, body: str):
    p = tmp_path / "rethlas.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_use_voi_scoring_defaults_to_false() -> None:
    cfg = SchedulingConfig()
    assert cfg.use_voi_scoring is False
    assert DEFAULT_USE_VOI_SCORING is False


def test_use_voi_scoring_omitted_keeps_default(tmp_path) -> None:
    p = _write_toml(tmp_path, "[scheduling]\ndesired_pass_count = 3\n")
    cfg = load_config(p)
    assert cfg.scheduling.use_voi_scoring is False


def test_use_voi_scoring_true_parsed(tmp_path) -> None:
    p = _write_toml(tmp_path, "[scheduling]\nuse_voi_scoring = true\n")
    cfg = load_config(p)
    assert cfg.scheduling.use_voi_scoring is True


def test_use_voi_scoring_false_parsed(tmp_path) -> None:
    p = _write_toml(tmp_path, "[scheduling]\nuse_voi_scoring = false\n")
    cfg = load_config(p)
    assert cfg.scheduling.use_voi_scoring is False


def test_use_voi_scoring_rejects_non_bool(tmp_path) -> None:
    p = _write_toml(tmp_path, '[scheduling]\nuse_voi_scoring = "yes"\n')
    with pytest.raises(ConfigError, match="must be a boolean"):
        load_config(p)


def test_use_voi_scoring_rejects_int(tmp_path) -> None:
    p = _write_toml(tmp_path, "[scheduling]\nuse_voi_scoring = 1\n")
    with pytest.raises(ConfigError, match="must be a boolean"):
        load_config(p)
