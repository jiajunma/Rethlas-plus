"""argparse setup + dispatch for ``rethlas-kb``.

Subcommand routing uses ``argparse`` defaults: each subparser sets a
``handler`` callable in its namespace, and ``main`` invokes whichever
handler the parsed command lands on. This keeps ``main`` short and
makes adding a new subcommand a single-call change in the relevant
``add_subparsers`` function.
"""

from __future__ import annotations

import argparse
import sys

from . import install, primitives, project_ops, workflows
from ._constants import EXIT_OK, EXIT_USAGE, __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)

    parser = _build_parser()
    if not args:
        parser.print_help(sys.stderr)
        return EXIT_OK

    ns = parser.parse_args(args)
    handler = getattr(ns, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    return handler(ns)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rethlas-kb",
        description=(
            "Agent layer on mdblueprint KB for new research mathematics.\n"
            "\n"
            "Two modes of use:\n"
            "  Mode A (primary)  — install slash commands "
            "(`rethlas-kb install-commands`) and run agents inside "
            "claude / codex / opencode.\n"
            "  Mode B (batch/CI) — `verify-stmt` etc. run a Python "
            "orchestrator end-to-end."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "-V", "--version", action="version",
        version=f"rethlas-kb {__version__}",
    )

    sub = p.add_subparsers(dest="command", metavar="<command>")

    # Mode B first in source order; Mode A primitives second. Argparse
    # lists them in registration order in the help text.
    workflows.add_subparsers(sub)
    project_ops.add_subparsers(sub)
    primitives.add_subparsers(sub)
    install.add_subparser(sub)

    return p


def _register_backends() -> None:
    """Self-register every real backend so ``get_backend`` can find them.

    Idempotent per-name — if a test (or earlier call) already registered
    a backend under "codex" or "claude" we leave it alone. The registry
    treats re-registration as overwrite, so we'd otherwise clobber a
    test's pre-staged MockBackend.
    """
    from rethlas_kb.backends import available_backends as _available
    from rethlas_kb.backends import claude as _claude_backend
    from rethlas_kb.backends import codex as _codex_backend

    existing = set(_available())
    if "codex" not in existing:
        _codex_backend.register_default()
    if "claude" not in existing:
        _claude_backend.register_default()


def _help_text() -> str:
    """Kept for backwards-compat with the v0.0.1 stub."""
    return _build_parser().format_help()
