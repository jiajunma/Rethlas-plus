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
    """Subcommands whose owning milestone hasn't shipped yet must still be
    reachable from argparse and print a recognisable placeholder.

    M3 wired init / add-node / revise-node / attach-hint / rebuild to real
    implementations; those are exercised in M3's own system tests and
    excluded from this placeholder check.
    """
    wired_in_m3 = {"init", "add-node", "revise-node", "attach-hint", "rebuild"}
    wired_in_m4 = {"librarian"}  # daemon entry — needs a workspace to run
    wired_in_m6 = {"generator"}  # CLI form needs --target/--mode args
    wired_in_m7 = {"verifier"}   # CLI form needs --target arg
    wired_in_m8 = {"supervise"}  # long-running daemon — needs workspace
    wired_in_m9 = {"dashboard"}  # standalone HTTP server — needs workspace
    wired_in_m10 = {"linter"}  # consistency audit — needs workspace
    wired = (
        wired_in_m3 | wired_in_m4 | wired_in_m6
        | wired_in_m7 | wired_in_m8 | wired_in_m9 | wired_in_m10
    )
    remaining = [n for n in SUBCOMMANDS if n not in wired]
    for name in remaining:
        result = _run(name)
        assert result.returncode == 0, (
            f"rethlas {name} returned {result.returncode}; stderr={result.stderr!r}"
        )
        assert "placeholder" in result.stdout.lower(), (
            f"rethlas {name} did not print a placeholder; stdout={result.stdout!r}"
        )
