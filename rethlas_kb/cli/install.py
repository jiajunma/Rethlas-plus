"""``install-commands`` subcommand — copy slash-command templates to a target CLI.

rethlas-kb ships slash-command markdown templates under
:mod:`rethlas_kb._commands` (one subdirectory per agentic CLI:
``claude``, ``codex``, ``opencode``). ``install-commands`` copies
them into the target CLI's command directory so users can invoke
``/verify-stmt …`` directly without writing the wrapper themselves.

The source dir is discovered via :mod:`importlib.resources`, so the
subcommand works equally for editable and wheel installs.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

from ._constants import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE

# What we ship + where each CLI looks for slash commands.
_TARGETS = ("claude", "codex", "opencode")

# Convention: ``user`` scope installs into ``~/.<cli>/commands/``,
# ``project`` scope installs into ``./.<cli>/commands/`` relative to
# ``--project`` (defaults to cwd). Both the user dirs and project
# dirs are what claude / codex / opencode look at out of the box.
_USER_DIRS = {
    "claude": Path.home() / ".claude" / "commands",
    "codex": Path.home() / ".codex" / "commands",
    "opencode": Path.home() / ".opencode" / "commands",
}


def add_subparser(sub) -> None:
    """Register ``install-commands`` on a parent ``subparsers`` object."""
    p = sub.add_parser(
        "install-commands",
        help="Install Mode A slash-command templates into a target agentic CLI.",
        description=(
            "Copies markdown templates from rethlas_kb._commands/{claude,"
            "codex,opencode}/ into the target CLI's command directory. "
            "Existing files are preserved unless --force is passed. "
            "Use --dry-run to preview without writing."
        ),
    )
    p.add_argument(
        "--target", action="append", choices=_TARGETS + ("all",),
        default=None,
        help=(
            "Which CLI(s) to install for. Repeat to install multiple, "
            "or pass 'all'. Default: 'all'."
        ),
    )
    p.add_argument(
        "--scope", choices=("user", "project"), default="user",
        help=(
            "user → ~/.<cli>/commands/ ; project → ./<project>/.<cli>/commands/ "
            "(default: user)."
        ),
    )
    p.add_argument(
        "--project", default=".",
        help="Project root for --scope project (default: cwd).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen, make no changes.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Overwrite existing files instead of skipping them.",
    )
    p.set_defaults(handler=_cmd_install_commands)


def _cmd_install_commands(ns: argparse.Namespace) -> int:
    targets = _resolve_targets(ns.target)
    if not targets:
        print("rethlas-kb: --target produced an empty list", file=sys.stderr)
        return EXIT_USAGE

    plan = _plan_install(
        targets=targets, scope=ns.scope, project_root=ns.project,
    )
    if not plan:
        print("rethlas-kb: no command files to install", file=sys.stderr)
        return EXIT_RUNTIME

    return _execute_plan(plan, dry_run=ns.dry_run, force=ns.force)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
def _resolve_targets(target_arg: list[str] | None) -> list[str]:
    """Expand the --target argument list into concrete CLI names."""
    if not target_arg or "all" in target_arg:
        return list(_TARGETS)
    # Preserve order, dedupe, keep only valid names.
    seen: list[str] = []
    for t in target_arg:
        if t in _TARGETS and t not in seen:
            seen.append(t)
    return seen


def _plan_install(
    *, targets: list[str], scope: str, project_root: str,
) -> list[tuple[str, Path, Path]]:
    """Build a list of ``(target, source_path, dest_path)`` triples."""
    plan: list[tuple[str, Path, Path]] = []
    for target in targets:
        source_files = _source_files_for(target)
        dest_dir = _dest_dir_for(target, scope=scope, project_root=project_root)
        for source in source_files:
            plan.append((target, source, dest_dir / source.name))
    return plan


def _source_files_for(target: str) -> list[Path]:
    """Return concrete filesystem paths for every .md under the target subpkg."""
    pkg = f"rethlas_kb._commands.{target}"
    try:
        anchor = resources.files(pkg)
    except (ModuleNotFoundError, FileNotFoundError):
        return []
    out: list[Path] = []
    for entry in sorted(anchor.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".md"):
            continue
        # ``as_file`` is the safe way to get a real Path; for editable
        # installs it returns the on-disk path directly.
        with resources.as_file(entry) as p:
            out.append(Path(p))
    return out


def _dest_dir_for(target: str, *, scope: str, project_root: str) -> Path:
    if scope == "user":
        return _USER_DIRS[target]
    root = Path(project_root).expanduser().resolve()
    return root / f".{target}" / "commands"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _execute_plan(
    plan: list[tuple[str, Path, Path]], *, dry_run: bool, force: bool,
) -> int:
    """Apply the install plan; return EXIT_OK on success."""
    installed = 0
    skipped = 0
    written: list[Path] = []
    for target, source, dest in plan:
        marker = _decide_action(dest, force=force)
        # Emit a per-file log on stderr (data on stdout would mean
        # "the list of installed paths" — see end of function)
        msg = f"[{target:8}] {marker} {dest}"
        print(msg, file=sys.stderr)
        if marker == "would-write" or marker == "would-overwrite":
            if not dry_run:
                _copy_with_parents(source, dest)
                written.append(dest)
            installed += 1
        else:
            skipped += 1

    summary = (
        f"{'DRY-RUN: ' if dry_run else ''}"
        f"{installed} installed, {skipped} skipped"
    )
    print(summary, file=sys.stderr)
    # Stdout: machine-readable list of files actually written
    for path in written:
        print(path)
    return EXIT_OK


def _decide_action(dest: Path, *, force: bool) -> str:
    if dest.exists():
        return "would-overwrite" if force else "skip-exists"
    return "would-write"


def _copy_with_parents(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


__all__ = ["add_subparser"]
