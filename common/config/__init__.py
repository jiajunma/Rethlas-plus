"""Workspace configuration: `rethlas.toml` parsing + validation (§2.4)."""

from common.config.loader import (
    ConfigError,
    RethlasConfig,
    SchedulingConfig,
    DashboardConfig,
    load_config,
)

__all__ = [
    "ConfigError",
    "RethlasConfig",
    "SchedulingConfig",
    "DashboardConfig",
    "load_config",
]
