"""rethlas-kb CLI entry point.

v0.0.2 — adds the first real subcommand, ``verify-stmt`` (issue #8).
Subsequent commands (``verify-proof``, ``fill-gap``,
``hunt-counterexample``, ``audit-source``) land in #9 / #10 / #11 /
#12 and slot into :func:`_build_parser` the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from rethlas_kb.adapter import KbAdapter
from rethlas_kb.backends import BackendError, get_backend
from rethlas_kb.backends import claude as _claude_backend
from rethlas_kb.backends import codex as _codex_backend
from rethlas_kb_agents.statement_verifier import (
    StatementReviewParseError,
    StatementVerifier,
)

__version__ = "0.0.2"

# Default backend when --backend is omitted. Matches the QED convention
# (codex is the workhorse). Users on Claude-only machines pass
# ``--backend claude`` explicitly.
DEFAULT_BACKEND = "codex"

# Exit codes — picked so shell pipelines can branch cleanly.
EXIT_OK = 0          # decision = accepted
EXIT_REVIEW_FAIL = 1  # decision != accepted (still a successful run)
EXIT_USAGE = 2        # bad CLI args
EXIT_RUNTIME = 3      # backend / parse / adapter failure


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)

    parser = _build_parser()
    if not args:
        parser.print_help(sys.stderr)
        return EXIT_OK

    ns = parser.parse_args(args)

    if ns.command == "verify-stmt":
        return _cmd_verify_stmt(ns)

    parser.print_help(sys.stderr)
    return EXIT_USAGE


# ---------------------------------------------------------------------------
# Subcommand: verify-stmt
# ---------------------------------------------------------------------------
def _cmd_verify_stmt(ns: argparse.Namespace) -> int:
    project = Path(ns.project).expanduser().resolve()
    if not (project / "docs" / "knowledge").exists():
        print(
            f"rethlas-kb: --project {project} has no docs/knowledge/ "
            f"directory — is this a mdblueprint repo?",
            file=sys.stderr,
        )
        return EXIT_USAGE

    _register_backends()
    try:
        backend = get_backend(ns.backend)
    except BackendError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    adapter = KbAdapter(project)
    verifier = StatementVerifier(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    try:
        review = verifier.run(ns.node_id, adapter)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except StatementReviewParseError as exc:
        print(
            f"rethlas-kb: backend output could not be parsed: {exc}",
            file=sys.stderr,
        )
        return EXIT_RUNTIME
    except BackendError as exc:
        print(f"rethlas-kb: backend error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if not ns.no_write:
        review_path = adapter.write_review(
            node_id=ns.node_id,
            agent_name="statement-verifier",
            review=_review_for_disk(review),
        )
        print(f"review written: {review_path}", file=sys.stderr)

    # The user gets the compact JSON verdict on stdout so they can pipe / jq.
    print(json.dumps(_review_for_stdout(review), indent=2, ensure_ascii=False))

    return EXIT_OK if review.is_accepted else EXIT_REVIEW_FAIL


def _review_for_stdout(review) -> dict:
    """Verdict-only view — no body, no raw LLM dump. Pipeable."""
    d = asdict(review)
    d.pop("raw", None)
    return d


def _review_for_disk(review) -> dict:
    """Disk view — verdict fields as frontmatter + raw LLM output as body."""
    d = asdict(review)
    raw = d.pop("raw", "") or ""
    d["body"] = (
        f"## Rationale\n\n{review.rationale}\n\n"
        f"## Raw LLM output\n\n```\n{raw}\n```\n"
    )
    return d


# ---------------------------------------------------------------------------
# Backend registration
# ---------------------------------------------------------------------------
def _register_backends() -> None:
    """Self-register every real backend so ``get_backend`` can find them.

    Called once per CLI invocation. Re-registration is a no-op-with-
    overwrite (registry semantics, issue #3), so tests can pre-stage
    a MockBackend and then call ``main`` without losing it — provided
    the test registers AFTER importing the cli module.
    """
    # Skip registration if a test (or a previous call) has already
    # registered a backend under the same name. The registry treats
    # re-registration as overwrite, so we'd clobber the test's mock.
    from rethlas_kb.backends import available_backends as _available
    existing = set(_available())
    if "codex" not in existing:
        _codex_backend.register_default()
    if "claude" not in existing:
        _claude_backend.register_default()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rethlas-kb",
        description=(
            "Agent layer on mdblueprint KB for new research mathematics."
        ),
    )
    p.add_argument(
        "-V", "--version",
        action="version",
        version=f"rethlas-kb {__version__}",
    )

    sub = p.add_subparsers(dest="command", metavar="<command>")

    # ---- verify-stmt --------------------------------------------------
    vs = sub.add_parser(
        "verify-stmt",
        help="Statement-verifier: judge whether a node's statement reads correctly.",
        description=(
            "Run the statement-verifier agent against one node. "
            "Writes a review under docs/knowledge/reviews/ and emits "
            "the verdict on stdout."
        ),
    )
    vs.add_argument(
        "node_id",
        help="Node id to verify (e.g. cellular_categories.sheaves_cosheaves)",
    )
    vs.add_argument(
        "--backend",
        choices=("codex", "claude"),
        default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    vs.add_argument(
        "--project",
        default=".",
        help=(
            "Path to the mdblueprint project root "
            "(the dir containing docs/knowledge/). Default: cwd."
        ),
    )
    vs.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Hard timeout for the backend invocation, in seconds.",
    )
    vs.add_argument(
        "--no-include-staged",
        dest="include_staged_context",
        action="store_false",
        help=(
            "Limit context to admitted nodes only "
            "(default: include staged nodes as non-admitted evidence)."
        ),
    )
    vs.add_argument(
        "--no-write",
        action="store_true",
        help="Print the review on stdout but don't persist it under reviews/.",
    )
    vs.set_defaults(include_staged_context=True)

    return p


# ---------------------------------------------------------------------------
# Help text fallback (used by older callers that imported _help_text)
# ---------------------------------------------------------------------------
def _help_text() -> str:
    """Kept for backwards-compat with the v0.0.1 stub."""
    return _build_parser().format_help()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
