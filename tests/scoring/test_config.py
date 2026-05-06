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


# ---------------------------------------------------------------------------
# S5 — policy budget caps + strong-verifier model id.
# ---------------------------------------------------------------------------
def test_policy_budget_caps_default_to_one() -> None:
    cfg = SchedulingConfig()
    assert cfg.policy_max_refute_per_node == 1
    assert cfg.policy_max_strong_per_node == 1


def test_policy_budget_caps_parsed_from_toml(tmp_path) -> None:
    p = _write_toml(
        tmp_path,
        "[scheduling]\n"
        "policy_max_refute_per_node = 2\n"
        "policy_max_strong_per_node = 3\n",
    )
    cfg = load_config(p)
    assert cfg.scheduling.policy_max_refute_per_node == 2
    assert cfg.scheduling.policy_max_strong_per_node == 3


def test_policy_budget_caps_accept_zero(tmp_path) -> None:
    """Setting a cap to 0 forces the corresponding rung to be skipped
    entirely (e.g. ``max_refute_per_node = 0`` makes DISAGREEMENT jump
    straight to STRONG_VERIFY)."""
    p = _write_toml(
        tmp_path,
        "[scheduling]\n"
        "policy_max_refute_per_node = 0\n"
        "policy_max_strong_per_node = 0\n",
    )
    cfg = load_config(p)
    assert cfg.scheduling.policy_max_refute_per_node == 0
    assert cfg.scheduling.policy_max_strong_per_node == 0


def test_policy_budget_caps_reject_negative(tmp_path) -> None:
    p = _write_toml(
        tmp_path, "[scheduling]\npolicy_max_refute_per_node = -1\n"
    )
    with pytest.raises(ConfigError, match="below minimum"):
        load_config(p)


def test_strong_verifier_model_id_defaults_empty() -> None:
    cfg = SchedulingConfig()
    assert cfg.strong_verifier_model_id == ""


def test_strong_verifier_model_id_parsed(tmp_path) -> None:
    p = _write_toml(
        tmp_path,
        '[scheduling]\nstrong_verifier_model_id = "claude-opus-thinking"\n',
    )
    cfg = load_config(p)
    assert cfg.scheduling.strong_verifier_model_id == "claude-opus-thinking"


def test_strong_verifier_model_id_rejects_non_string(tmp_path) -> None:
    p = _write_toml(tmp_path, "[scheduling]\nstrong_verifier_model_id = 42\n")
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(p)
