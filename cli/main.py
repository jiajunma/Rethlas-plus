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
import re
import sys
from typing import Sequence

# Mirror common.events.schema._ACTOR_RE so the CLI can fail at parse
# time instead of letting a malformed actor reach the librarian and
# get bounced via rejected_writes (which is wasted disk + confusing
# error far from the cause).
_CLI_ACTOR_RE = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.-]+$")
_AXIOM_KINDS_FOR_CLI = frozenset({"definition", "external_theorem"})


def _validate_actor(actor: str) -> str:
    if not _CLI_ACTOR_RE.match(actor):
        raise SystemExit(
            f"--actor {actor!r} must match kind:instance "
            f"(e.g. user:cli, generator:codex-default). "
            f"Bare 'user' is not valid; use --actor user:cli."
        )
    return actor


def _validate_axiom_no_proof(kind: str, proof: str) -> None:
    if kind in _AXIOM_KINDS_FOR_CLI and proof:
        raise SystemExit(
            f"--proof must be empty for kind={kind!r} (axioms carry no proof per ARCH §5.1). "
            f"Move any well-formedness justification to --remark."
        )


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
    "learner": "run a learner source-ingestion attempt (Phase 3)",
    "referee": "run a referee review attempt (Phase 3)",
    "bridge-repair": "create Phase 3 bridge-repair overlays",
    "review": "list, show, graph, or typo-inspect Phase 3 referee review artifacts",
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

    # learner (Phase 3 — standalone CLI form)
    sp = sub.add_parser(
        "learner", help=SUBCOMMANDS["learner"], description=SUBCOMMANDS["learner"]
    )
    sp.add_argument("--source", default="")
    sp.add_argument("--context-json", default="")
    sp.add_argument("--max-nodes", type=int, default=30)
    sp.add_argument("--queue", action="store_true")
    sp.add_argument("--codex-argv", default="")
    sp.add_argument("--silent-timeout-s", type=float, default=1800.0)
    sp.add_argument("--actor", default="learner:cli")

    # referee (Phase 3 — standalone CLI form)
    sp = sub.add_parser(
        "referee", help=SUBCOMMANDS["referee"], description=SUBCOMMANDS["referee"]
    )
    sp.add_argument("--target", default="")
    sp.add_argument("--source", default="")
    sp.add_argument("--context-json", default="")
    sp.add_argument("--queue", action="store_true")
    sp.add_argument("--codex-argv", default="")
    sp.add_argument("--silent-timeout-s", type=float, default=1800.0)
    sp.add_argument("--actor", default="referee:cli")

    # bridge-repair (Phase 3 — study graph overlay)
    sp = sub.add_parser(
        "bridge-repair",
        help=SUBCOMMANDS["bridge-repair"],
        description=SUBCOMMANDS["bridge-repair"],
    )
    sp.add_argument("--auto", action="store_true", help="apply conservative built-in repairs")
    sp.add_argument(
        "--alias",
        dest="aliases",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="redirect one label to a canonical label",
    )
    sp.add_argument(
        "--close-resolved",
        action="store_true",
        help="close bridge requests whose blocked nodes now have extracted source proofs",
    )
    sp.add_argument(
        "--materialize-plan",
        action="store_true",
        help="record repair-plan items for still-open bridge requests",
    )
    sp.add_argument(
        "--enqueue-plans",
        action="store_true",
        help="enqueue active bridge materialization plans for Phase 3 learner repair",
    )
    sp.add_argument(
        "--max-enqueue",
        type=int,
        default=None,
        help="maximum bridge repair learner items to enqueue",
    )
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--actor", default="bridge-repair:cli")

    # review (Phase 3 — read-only review artifacts)
    sp = sub.add_parser("review", help=SUBCOMMANDS["review"], description=SUBCOMMANDS["review"])
    review_sub = sp.add_subparsers(dest="review_command", metavar="<review-command>")
    review_sub.add_parser("list", help="list review artifacts")
    rsp = review_sub.add_parser("show", help="show one review artifact as JSON")
    rsp.add_argument("review_id")
    rsp = review_sub.add_parser("graph", help="show theorem graph from one review")
    rsp.add_argument("review_id")
    rsp.add_argument(
        "--format",
        choices=("text", "json", "mermaid", "dot"),
        default="text",
        help="graph output format (default: text)",
    )
    rsp = review_sub.add_parser("typos", help="show typo findings from one review")
    rsp.add_argument("review_id")
    repair_sub = review_sub.add_parser(
        "repair",
        help="create or list source-node repair overlays for review theorem graphs",
    )
    repair_actions = repair_sub.add_subparsers(dest="repair_action", metavar="<repair-action>")
    rsp = repair_actions.add_parser("run", help="repair review theorem-node display fields")
    rsp.add_argument("review_id")
    rsp.add_argument("--label", dest="labels", action="append", default=[])
    rsp.add_argument("--all-needs-repair", action="store_true")
    rsp.add_argument("--actor", default="review-repair:cli")
    repair_actions.add_parser("list", help="list review repair overlays")

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

    # linter (M10)
    sp = sub.add_parser(
        "linter", help=SUBCOMMANDS["linter"], description=SUBCOMMANDS["linter"]
    )
    sp.add_argument(
        "--repair-nodes",
        action="store_true",
        dest="repair_nodes",
        help="rewrite divergent nodes/*.md and remove orphans (category E only)",
    )
    sp.add_argument(
        "--allow-concurrent",
        action="store_true",
        dest="allow_concurrent",
        help="run while supervise lock is held (drift entries may be transient)",
    )

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
        _validate_actor(args.actor)
        _validate_axiom_no_proof(args.kind, args.proof)
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
        _validate_actor(args.actor)
        _validate_axiom_no_proof(args.kind, args.proof)
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
        _validate_actor(args.actor)
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

    if args.command == "learner":
        from learner.cli import run_learner
        return run_learner(ws, args)

    if args.command == "referee":
        from referee.cli import run_referee
        return run_referee(ws, args)

    if args.command == "bridge-repair":
        _validate_actor(args.actor)
        from cli.bridge_repair import run_bridge_repair
        return run_bridge_repair(ws, args)

    if args.command == "review":
        from cli.review import run_review
        return run_review(ws, args)

    if args.command == "supervise":
        from coordinator.main import run_supervise
        return run_supervise(ws)

    if args.command == "dashboard":
        from dashboard.cli import run_dashboard
        return run_dashboard(ws, args)

    if args.command == "linter":
        from cli.linter import run_linter
        return run_linter(ws, args)

    return _run_stub(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
