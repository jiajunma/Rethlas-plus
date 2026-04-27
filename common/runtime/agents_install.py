"""Materialize the Phase I agent tree into a Rethlas workspace.

ARCHITECTURE §6.2 / §6.3 require Codex to execute under the project's
own ``AGENTS.md``, ``.codex/config.toml``, and skill set. By default
``codex exec`` resolves config / agents from cwd upwards, so if cwd
sits in an arbitrary workspace, none of those files are reachable
and the agent runs with default-Codex behavior (no Phase I MCP, no
Phase I skills, no Phase I prompt contract). H22 fixed that by
copying the agent tree into the workspace at ``rethlas init`` time:
``<workspace>/agents/{generation,verification}/`` becomes the
worker-side cwd, so the agent only ever sees workspace-resident
files (no risk of escaping into the user's home dir or into a
sibling project's old result artifacts).

This module owns the copy step and the source-repo lookup. The
``copytree`` filter intentionally skips runtime-only directories
(``.venv/``, ``memory/``, ``logs/``, ``results/``, ``data/``,
``site/``, ``scripts/``, ``tests/``, ``__pycache__``, ``.obsidian``,
``.bin``, ``.vendor``, ``downloads``) so the materialized copy is
small and free of operator-specific state.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

# Sub-paths under ``<source>/agents/<kind>/`` that we deliberately skip when
# materializing into the workspace. Anything not listed is copied verbatim.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        ".bin",
        ".vendor",
        ".obsidian",
        "__pycache__",
        "memory",
        "logs",
        "results",
        "data",
        "site",
        "scripts",
        "tests",
        "downloads",
        "examples",
    }
)

# Files that are noise under the materialized agent dir (editor / OS state).
_SKIP_FILES: frozenset[str] = frozenset({".DS_Store"})

_AGENT_KINDS: tuple[str, ...] = ("generation", "verification")


def source_repo_root() -> Path:
    """Return the rethlas source repo root that ships this package.

    Resolves via ``Path(__file__).parents[2]`` — ``common/runtime/`` lives
    inside the source repo regardless of whether rethlas was installed
    via ``pip install -e .`` or copied somewhere. Pinning this in code
    means the workspace materializer never asks the user for a path.
    """
    return Path(__file__).resolve().parents[2]


def source_agents_dir(repo_root: Path | None = None) -> Path:
    return (repo_root or source_repo_root()) / "agents"


def workspace_agents_dir(workspace_root: Path) -> Path:
    return workspace_root / "agents"


def agent_kind_dir(workspace_root: Path, kind: str) -> Path:
    if kind not in _AGENT_KINDS:
        raise ValueError(f"unknown agent kind: {kind!r} (expected one of {_AGENT_KINDS})")
    return workspace_agents_dir(workspace_root) / kind


def _ignore(directory: str, names: Iterable[str]) -> set[str]:
    skipped: set[str] = set()
    for name in names:
        full = Path(directory) / name
        if name in _SKIP_FILES:
            skipped.add(name)
            continue
        if full.is_dir() and name in _SKIP_DIRS:
            skipped.add(name)
    return skipped


def materialize_agents(
    *,
    workspace_root: Path,
    repo_root: Path | None = None,
    overwrite: bool = True,
) -> list[Path]:
    """Copy ``<repo>/agents/{generation,verification}/`` into the workspace.

    Returns the list of materialized agent dirs (one per kind). Idempotent:
    when ``overwrite=True`` (the default), an existing ``<workspace>/agents``
    is removed first so stale skill files don't linger after an upstream
    update. ``overwrite=False`` preserves existing dirs (used when the
    operator has hand-edited skills under the workspace and re-runs
    ``rethlas init --force`` without wanting their changes overwritten).
    """
    repo = (repo_root or source_repo_root()).resolve()
    src_root = source_agents_dir(repo)
    if not src_root.is_dir():
        raise FileNotFoundError(
            f"rethlas source agents directory not found at {src_root}; "
            "is the package installed from a development checkout?"
        )

    materialized: list[Path] = []
    for kind in _AGENT_KINDS:
        src = src_root / kind
        if not src.is_dir():
            raise FileNotFoundError(f"missing source agent dir: {src}")
        dst = agent_kind_dir(workspace_root, kind)
        if dst.exists() and overwrite:
            shutil.rmtree(dst)
        if dst.exists():
            # overwrite=False and dst exists → leave alone, but still report it.
            materialized.append(dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, ignore=_ignore, symlinks=False)
        materialized.append(dst)
    return materialized


__all__ = [
    "agent_kind_dir",
    "materialize_agents",
    "source_agents_dir",
    "source_repo_root",
    "workspace_agents_dir",
]
