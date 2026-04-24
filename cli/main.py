"""Rethlas CLI entry point.

M0 stub dispatcher: every Phase I subcommand is registered with
argparse so `rethlas --help` lists them. Each subcommand prints a
placeholder line and exits 0 until the owning milestone replaces it.
Real implementations land in M3 (init / add-node / revise-node /
attach-hint / rebuild), M4 (librarian), M6 (generator), M7 (verifier),
M8 (supervise), M9 (dashboard), M10 (linter).
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

SUBCOMMANDS: dict[str, str] = {
    "init": "initialize a Rethlas workspace (M3)",
    "add-node": "publish a user.node_added event (M3)",
    "revise-node": "publish a user.node_revised event (M3)",
    "attach-hint": "publish a user.hint_attached event (M3)",
    "supervise": "run the coordinator + librarian + dashboard (M8)",
    "dashboard": "run the read-only dashboard standalone (M9)",
    "linter": "run the workspace linter (M10)",
    "rebuild": "rebuild the projected KB from events/ (M3 / M4)",
    "generator": "internal generator worker entry (M6)",
    "verifier": "internal verifier worker entry (M7)",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rethlas",
        description="Rethlas — event-sourced knowledge base with LLM workers.",
    )
    parser.add_argument(
        "--workspace",
        metavar="PATH",
        help="path to the Rethlas workspace (default: current directory)",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    for name, help_text in SUBCOMMANDS.items():
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(_cmd=name)

    return parser


def _run_stub(name: str) -> int:
    """Placeholder for every Phase I subcommand until its owning milestone lands."""
    sys.stdout.write(f"rethlas {name}: placeholder (not yet implemented in this milestone)\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        return 1

    return _run_stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
