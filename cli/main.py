"""Rethlas CLI entry point.

Wired subcommands (M3 delivers user-visible publish + lifecycle):
- ``init``        -> cli.init
- ``add-node``    -> cli.add_node
- ``revise-node`` -> cli.revise_node
- ``attach-hint`` -> cli.attach_hint
- ``rebuild``     -> cli.rebuild

Remaining Phase I subcommands stay as placeholders until their owning
milestone lands (``supervise`` M8, ``dashboard`` M9, ``linter`` M10,
``generator`` M6 worker entry, ``verifier`` M7 worker entry).
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
    "supervise": "run the coordinator + librarian (M8)",
    "dashboard": "run the read-only dashboard standalone (M9)",
    "linter": "run the workspace linter (M10)",
    "rebuild": "rebuild the projected KB from events/ (M3 / M4)",
    "librarian": "internal librarian daemon entry (M4)",
    "generator": "run a generator attempt against the workspace (M6)",
    "verifier": "run a verifier attempt against the workspace (M7)",
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

    # init
    sp = sub.add_parser("init", help=SUBCOMMANDS["init"], description=SUBCOMMANDS["init"])
    sp.add_argument("--force", action="store_true", help="overwrite rethlas.toml if present")

    # add-node
    sp = sub.add_parser("add-node", help=SUBCOMMANDS["add-node"], description=SUBCOMMANDS["add-node"])
    sp.add_argument("--label", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--proof", default="")
    sp.add_argument("--remark", default="")
    sp.add_argument("--source-note", default="")
    sp.add_argument("--actor", default="user:cli", help="producer actor (default user:cli)")

    # revise-node
    sp = sub.add_parser("revise-node", help=SUBCOMMANDS["revise-node"], description=SUBCOMMANDS["revise-node"])
    sp.add_argument("--label", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--proof", default="")
    sp.add_argument("--remark", default="")
    sp.add_argument("--source-note", default="")
    sp.add_argument("--actor", default="user:cli")

    # attach-hint
    sp = sub.add_parser("attach-hint", help=SUBCOMMANDS["attach-hint"], description=SUBCOMMANDS["attach-hint"])
    sp.add_argument("--target", required=True)
    sp.add_argument("--hint", required=True)
    sp.add_argument("--actor", default="user:cli")

    # rebuild
    sp = sub.add_parser("rebuild", help=SUBCOMMANDS["rebuild"], description=SUBCOMMANDS["rebuild"])

    # librarian (internal — invoked by coordinator as a subprocess)
    sp = sub.add_parser(
        "librarian", help=SUBCOMMANDS["librarian"], description=SUBCOMMANDS["librarian"]
    )

    # generator (M6 — standalone CLI form)
    sp = sub.add_parser(
        "generator", help=SUBCOMMANDS["generator"], description=SUBCOMMANDS["generator"]
    )
    sp.add_argument("--target", required=True)
    sp.add_argument("--mode", required=True, choices=("fresh", "repair"))
    sp.add_argument("--codex-argv", default="")
    sp.add_argument("--silent-timeout-s", type=float, default=1800.0)
    sp.add_argument("--actor", default="generator:cli")

    # verifier (M7 — standalone CLI form)
    sp = sub.add_parser(
        "verifier", help=SUBCOMMANDS["verifier"], description=SUBCOMMANDS["verifier"]
    )
    sp.add_argument("--target", required=True)
    sp.add_argument("--codex-argv", default="")
    sp.add_argument("--silent-timeout-s", type=float, default=1800.0)
    sp.add_argument("--actor", default="verifier:cli")

    # supervise (M8)
    sp = sub.add_parser(
        "supervise", help=SUBCOMMANDS["supervise"], description=SUBCOMMANDS["supervise"]
    )

    # dashboard (M9 — standalone HTTP server)
    sp = sub.add_parser(
        "dashboard", help=SUBCOMMANDS["dashboard"], description=SUBCOMMANDS["dashboard"]
    )
    sp.add_argument(
        "--bind",
        default="",
        help="HOST:PORT (default: rethlas.toml [dashboard] bind = 127.0.0.1:8765)",
    )

    # still-placeholder subcommands
    for name in ("linter",):
        sp = sub.add_parser(name, help=SUBCOMMANDS[name], description=SUBCOMMANDS[name])

    return parser


def _run_stub(name: str) -> int:
    sys.stdout.write(
        f"rethlas {name}: placeholder (not yet implemented in this milestone)\n"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        return 1

    ws = getattr(args, "workspace", None)

    if args.command == "init":
        from cli.init import run_init
        return run_init(ws, force=getattr(args, "force", False))

    if args.command == "add-node":
        from cli.add_node import run_add_node
        return run_add_node(
            workspace=ws,
            label=args.label,
            kind=args.kind,
            statement=args.statement,
            proof=args.proof,
            remark=args.remark,
            source_note=args.source_note,
            actor=args.actor,
        )

    if args.command == "revise-node":
        from cli.revise_node import run_revise_node
        return run_revise_node(
            workspace=ws,
            label=args.label,
            kind=args.kind,
            statement=args.statement,
            proof=args.proof,
            remark=args.remark,
            source_note=args.source_note,
            actor=args.actor,
        )

    if args.command == "attach-hint":
        from cli.attach_hint import run_attach_hint
        return run_attach_hint(
            workspace=ws,
            target=args.target,
            hint=args.hint,
            actor=args.actor,
        )

    if args.command == "rebuild":
        from cli.rebuild import run_rebuild
        return run_rebuild(ws)

    if args.command == "librarian":
        from librarian.cli import run_librarian
        return run_librarian(ws)

    if args.command == "generator":
        from generator.cli import run_generator
        return run_generator(ws, args)

    if args.command == "verifier":
        from verifier.cli import run_verifier
        return run_verifier(ws, args)

    if args.command == "supervise":
        from coordinator.main import run_supervise
        return run_supervise(ws)

    if args.command == "dashboard":
        from dashboard.cli import run_dashboard
        return run_dashboard(ws, args)

    # placeholders still — linter
    return _run_stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
