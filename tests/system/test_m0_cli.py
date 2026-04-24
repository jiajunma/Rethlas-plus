"""M0 — system tests for the stub CLI dispatcher.

These tests invoke the CLI via ``python -m cli.main`` so they exercise
exactly the code path ``rethlas`` resolves to after ``pip install``.
"""

from __future__ import annotations

import subprocess
import sys

from cli.main import SUBCOMMANDS

PYTHON = sys.executable


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, "-m", "cli.main", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_lists_every_subcommand() -> None:
    """``rethlas --help`` must mention every Phase I subcommand by name."""
    result = _run("--help")
    assert result.returncode == 0
    out = result.stdout
    for name in SUBCOMMANDS:
        assert name in out, f"subcommand {name!r} missing from --help output"


def test_no_subcommand_prints_help_to_stderr_exit_1() -> None:
    """``rethlas`` with no subcommand: help to stderr, exit 1 (argparse convention)."""
    result = _run()
    assert result.returncode == 1
    assert "usage:" in result.stderr.lower()


def test_each_stub_subcommand_is_reachable() -> None:
    """Every subcommand's ``--help`` must exit 0 with non-empty stdout."""
    for name in SUBCOMMANDS:
        result = _run(name, "--help")
        assert result.returncode == 0, (
            f"rethlas {name} --help returned {result.returncode}; "
            f"stderr={result.stderr!r}"
        )
        assert result.stdout.strip(), f"rethlas {name} --help produced no stdout"


def test_each_stub_subcommand_runs_placeholder() -> None:
    """Invoking each subcommand (no args) must exit 0 and print a placeholder line."""
    for name in SUBCOMMANDS:
        result = _run(name)
        assert result.returncode == 0, (
            f"rethlas {name} returned {result.returncode}; stderr={result.stderr!r}"
        )
        assert "placeholder" in result.stdout.lower(), (
            f"rethlas {name} did not print a placeholder; stdout={result.stdout!r}"
        )
