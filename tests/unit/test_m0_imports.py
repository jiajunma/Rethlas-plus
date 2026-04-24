"""M0 — import smoke for every top-level package and common subpackage."""

from __future__ import annotations

import importlib

import pytest

TOP_LEVEL_PACKAGES = [
    "cli",
    "common",
    "coordinator",
    "dashboard",
    "generator",
    "librarian",
    "linter",
    "verifier",
]

COMMON_SUBPACKAGES = [
    "common.config",
    "common.events",
    "common.kb",
    "common.runtime",
]


@pytest.mark.parametrize("name", TOP_LEVEL_PACKAGES + COMMON_SUBPACKAGES)
def test_package_imports(name: str) -> None:
    importlib.import_module(name)


def test_cli_main_callable() -> None:
    from cli import main

    assert callable(main.main)


def test_subcommand_registry_complete() -> None:
    """Every Phase I subcommand named in M0 must be in the CLI registry."""
    from cli.main import SUBCOMMANDS

    required = {
        "init",
        "add-node",
        "revise-node",
        "attach-hint",
        "supervise",
        "dashboard",
        "linter",
        "rebuild",
        "generator",
        "verifier",
    }
    assert required.issubset(set(SUBCOMMANDS)), (
        f"missing subcommands: {required - set(SUBCOMMANDS)}"
    )
