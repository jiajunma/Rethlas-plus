"""Parse ``rethlas.toml`` with validation bounds from ARCHITECTURE §2.4.

Contract:
- A missing file falls back to all defaults.
- Missing sections / fields fall back to their documented default.
- Unknown fields inside known sections emit a warning (via the standard
  ``logging`` module) but do not fail; their values are ignored.
- Unknown top-level sections are warned about and ignored.
- Malformed TOML or out-of-bounds values raise :class:`ConfigError`,
  which callers (e.g. the CLI) translate to exit code 4.

The parser is intentionally small and dependency-free (uses stdlib
``tomllib`` on 3.11+) so it works in any process that needs config —
user CLI, coordinator, librarian, dashboard, linter — without cross
coupling.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Mapping

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import]
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found]

log = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when ``rethlas.toml`` is malformed or violates §2.4 bounds."""


# ---------------------------------------------------------------------------
# Defaults — single source of truth for both parsing and the init template.
# ---------------------------------------------------------------------------
DEFAULT_DESIRED_PASS_COUNT: Final[int] = 3
DEFAULT_GENERATOR_WORKERS: Final[int] = 2
DEFAULT_VERIFIER_WORKERS: Final[int] = 4
DEFAULT_CODEX_SILENT_TIMEOUT_SECONDS: Final[int] = 1800
DEFAULT_DASHBOARD_BIND: Final[str] = "127.0.0.1:8765"

# Known keys per section (anything else triggers an "unknown field" warning).
_SCHEDULING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "desired_pass_count",
        "generator_workers",
        "verifier_workers",
        "codex_silent_timeout_seconds",
    }
)
_DASHBOARD_KEYS: Final[frozenset[str]] = frozenset({"bind"})
_KNOWN_SECTIONS: Final[frozenset[str]] = frozenset({"scheduling", "dashboard"})


# ---------------------------------------------------------------------------
# Dataclasses — immutable runtime view of the config.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SchedulingConfig:
    desired_pass_count: int = DEFAULT_DESIRED_PASS_COUNT
    generator_workers: int = DEFAULT_GENERATOR_WORKERS
    verifier_workers: int = DEFAULT_VERIFIER_WORKERS
    codex_silent_timeout_seconds: int = DEFAULT_CODEX_SILENT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class DashboardConfig:
    bind: str = DEFAULT_DASHBOARD_BIND


@dataclass(frozen=True, slots=True)
class RethlasConfig:
    scheduling: SchedulingConfig = field(default_factory=SchedulingConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)


# ---------------------------------------------------------------------------
# Public loader.
# ---------------------------------------------------------------------------
def load_config(path: str | Path | None) -> RethlasConfig:
    """Load and validate a ``rethlas.toml`` file.

    Parameters
    ----------
    path:
        Path to ``rethlas.toml``. If it does not exist, returns an
        all-defaults :class:`RethlasConfig`. If ``None`` and no such
        file is found, same behaviour.

    Raises
    ------
    ConfigError
        On malformed TOML or out-of-range values (§2.4). Callers should
        map this to exit code 4.
    """
    if path is None:
        return RethlasConfig()

    p = Path(path)
    if not p.is_file():
        return RethlasConfig()

    try:
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:  # type: ignore[attr-defined]
        raise ConfigError(f"malformed TOML in {p}: {exc}") from exc

    return _from_raw(raw)


# ---------------------------------------------------------------------------
# Internal parsing.
# ---------------------------------------------------------------------------
def _from_raw(raw: Mapping[str, Any]) -> RethlasConfig:
    # Warn on unknown top-level sections.
    for key in raw:
        if key not in _KNOWN_SECTIONS:
            log.warning("rethlas.toml: unknown section [%s]; ignoring", key)

    scheduling_raw = raw.get("scheduling", {})
    if not isinstance(scheduling_raw, Mapping):
        raise ConfigError("[scheduling] must be a table")

    dashboard_raw = raw.get("dashboard", {})
    if not isinstance(dashboard_raw, Mapping):
        raise ConfigError("[dashboard] must be a table")

    return RethlasConfig(
        scheduling=_parse_scheduling(scheduling_raw),
        dashboard=_parse_dashboard(dashboard_raw),
    )


def _parse_scheduling(raw: Mapping[str, Any]) -> SchedulingConfig:
    for key in raw:
        if key not in _SCHEDULING_KEYS:
            log.warning("rethlas.toml: unknown field [scheduling] %s; ignoring", key)

    return SchedulingConfig(
        desired_pass_count=_positive_int(
            raw, "desired_pass_count", DEFAULT_DESIRED_PASS_COUNT, minimum=1
        ),
        generator_workers=_positive_int(
            raw, "generator_workers", DEFAULT_GENERATOR_WORKERS, minimum=1
        ),
        verifier_workers=_positive_int(
            raw, "verifier_workers", DEFAULT_VERIFIER_WORKERS, minimum=1
        ),
        codex_silent_timeout_seconds=_positive_int(
            raw,
            "codex_silent_timeout_seconds",
            DEFAULT_CODEX_SILENT_TIMEOUT_SECONDS,
            minimum=60,
        ),
    )


def _parse_dashboard(raw: Mapping[str, Any]) -> DashboardConfig:
    for key in raw:
        if key not in _DASHBOARD_KEYS:
            log.warning("rethlas.toml: unknown field [dashboard] %s; ignoring", key)

    bind = raw.get("bind", DEFAULT_DASHBOARD_BIND)
    if not isinstance(bind, str):
        raise ConfigError(f"[dashboard] bind must be a string, got {type(bind).__name__}")
    _validate_bind(bind)
    return DashboardConfig(bind=bind)


def _positive_int(
    raw: Mapping[str, Any], key: str, default: int, *, minimum: int
) -> int:
    if key not in raw:
        return default

    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"[scheduling] {key} must be an integer, got {type(value).__name__}"
        )
    if value < minimum:
        raise ConfigError(
            f"[scheduling] {key} = {value} is below minimum {minimum} (§2.4)"
        )
    return value


# host:port where port is [1, 65535]. Host can be anything the OS might accept
# (IPv4 / IPv6 literal / hostname), so we just insist on non-empty and the
# right shape; a bad host is surfaced later when socket.bind() fails.
_BIND_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<host>.+):(?P<port>\d+)$")


def _validate_bind(bind: str) -> None:
    m = _BIND_RE.match(bind)
    if m is None:
        raise ConfigError(
            f"[dashboard] bind = {bind!r} must match HOST:PORT (e.g. 127.0.0.1:8765)"
        )
    try:
        port = int(m.group("port"))
    except ValueError as exc:  # pragma: no cover - regex guards this
        raise ConfigError(f"[dashboard] bind port not an integer: {bind!r}") from exc
    if not (1 <= port <= 65535):
        raise ConfigError(
            f"[dashboard] bind port {port} out of range (must be in 1..65535)"
        )
    if not m.group("host"):
        raise ConfigError(f"[dashboard] bind host is empty: {bind!r}")
