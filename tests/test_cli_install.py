"""``install-commands`` subcommand tests (issue #20)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from rethlas_kb import cli


def _run(argv: list[str]) -> tuple[int, str, str]:
    import sys
    old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = io.StringIO("")
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        rc = cli.main(argv)
        return rc, sys.stdout.getvalue(), sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err


# ---------------------------------------------------------------------------
# Help + listing
# ---------------------------------------------------------------------------
def test_install_commands_appears_in_top_level_help() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run(["--help"])
    assert exc_info.value.code == 0


def test_install_commands_help_works() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run(["install-commands", "--help"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------
def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    rc, out, err = _run([
        "install-commands", "--target", "claude",
        "--scope", "project", "--project", str(tmp_path),
        "--dry-run",
    ])
    assert rc == cli.EXIT_OK
    # No files created
    assert not (tmp_path / ".claude").exists()
    # But the plan was logged
    assert "would-write" in err
    assert "verify-stmt.md" in err
    assert "DRY-RUN" in err


def test_dry_run_does_not_print_paths_on_stdout(tmp_path: Path) -> None:
    """stdout is for *actually-written* paths — dry-run writes nothing."""
    rc, out, _ = _run([
        "install-commands", "--target", "claude",
        "--scope", "project", "--project", str(tmp_path),
        "--dry-run",
    ])
    assert rc == cli.EXIT_OK
    assert out.strip() == ""


# ---------------------------------------------------------------------------
# project-scope install
# ---------------------------------------------------------------------------
def test_install_project_scope_creates_command_file(tmp_path: Path) -> None:
    rc, out, _ = _run([
        "install-commands", "--target", "claude",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    installed = tmp_path / ".claude" / "commands" / "verify-stmt.md"
    assert installed.exists()
    text = installed.read_text()
    assert "statement-verifier" in text
    assert "rethlas-kb compose-prompt" in text
    # stdout should report the installed path
    assert str(installed) in out


def test_install_all_targets_creates_three_files(tmp_path: Path) -> None:
    rc, _, _ = _run([
        "install-commands", "--target", "all",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    for cli_name in ("claude", "codex", "opencode"):
        assert (tmp_path / f".{cli_name}" / "commands" / "verify-stmt.md").exists()


def test_install_default_target_is_all(tmp_path: Path) -> None:
    """Omitting --target installs everything."""
    rc, _, _ = _run([
        "install-commands",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    for cli_name in ("claude", "codex", "opencode"):
        assert (tmp_path / f".{cli_name}" / "commands" / "verify-stmt.md").exists()


def test_install_repeated_target_flag_dedupes(tmp_path: Path) -> None:
    rc, _, err = _run([
        "install-commands",
        "--target", "claude", "--target", "claude",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    # Despite passing claude twice, only one "would-write" line emitted
    assert err.count("would-write") == 1
    assert err.count("verify-stmt.md") == 1
    assert "1 installed, 0 skipped" in err


def test_install_multiple_targets_in_one_flag(tmp_path: Path) -> None:
    rc, _, _ = _run([
        "install-commands",
        "--target", "claude", "--target", "codex",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    assert (tmp_path / ".claude" / "commands" / "verify-stmt.md").exists()
    assert (tmp_path / ".codex" / "commands" / "verify-stmt.md").exists()
    # opencode NOT installed
    assert not (tmp_path / ".opencode").exists()


# ---------------------------------------------------------------------------
# Existing-file handling
# ---------------------------------------------------------------------------
def test_install_skips_existing_files_by_default(tmp_path: Path) -> None:
    dest = tmp_path / ".claude" / "commands" / "verify-stmt.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("USER CUSTOM CONTENT — do not overwrite\n")

    rc, _, err = _run([
        "install-commands", "--target", "claude",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    assert "skip-exists" in err
    # File content preserved
    assert dest.read_text().startswith("USER CUSTOM CONTENT")


def test_install_force_overwrites_existing_files(tmp_path: Path) -> None:
    dest = tmp_path / ".claude" / "commands" / "verify-stmt.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("old content\n")

    rc, _, err = _run([
        "install-commands", "--target", "claude", "--force",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    assert "would-overwrite" in err
    text = dest.read_text()
    assert "old content" not in text
    assert "statement-verifier" in text


# ---------------------------------------------------------------------------
# Plan-level invariants
# ---------------------------------------------------------------------------
def test_install_summary_line_format(tmp_path: Path) -> None:
    rc, _, err = _run([
        "install-commands", "--target", "codex",
        "--scope", "project", "--project", str(tmp_path),
    ])
    assert rc == cli.EXIT_OK
    # Summary line: "<N> installed, <M> skipped"
    last_line = [line for line in err.strip().split("\n") if "installed" in line][-1]
    assert "installed" in last_line and "skipped" in last_line


def test_install_user_scope_targets_home_dir() -> None:
    """User-scope plan resolves to ~/.<cli>/commands/ — we test it via dry-run."""
    rc, _, err = _run([
        "install-commands", "--target", "claude", "--scope", "user",
        "--dry-run",
    ])
    assert rc == cli.EXIT_OK
    home = str(Path.home())
    assert home in err
    assert ".claude/commands" in err
