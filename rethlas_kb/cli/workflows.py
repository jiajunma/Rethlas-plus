"""Mode B subcommands — Python orchestrates the LLM end-to-end.

For interactive use prefer Mode A (slash commands installed by
``rethlas-kb install-commands``). Mode B is for batch / CI /
determinism — when you want a single Python process to make the
LLM call, parse the verdict, and exit with a status code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from rethlas_kb.backends import BackendError, get_backend
from rethlas_kb_agents.statement_verifier import (
    StatementReviewParseError,
    StatementVerifier,
)

from ._constants import (
    DEFAULT_BACKEND,
    EXIT_OK,
    EXIT_REVIEW_FAIL,
    EXIT_RUNTIME,
    EXIT_USAGE,
)
from ._io import adapter_for


def add_subparsers(sub) -> None:
    """Register every Mode B subcommand on a parent ``subparsers`` object."""
    _add_verify_stmt(sub)


# ---------------------------------------------------------------------------
# verify-stmt
# ---------------------------------------------------------------------------
def _add_verify_stmt(sub) -> None:
    vs = sub.add_parser(
        "verify-stmt",
        help=(
            "(Mode B) Statement-verifier: judge whether a node's statement "
            "reads correctly."
        ),
        description=(
            "Mode B: Python orchestrates the LLM call end-to-end. "
            "For interactive use prefer Mode A — see "
            "`rethlas-kb install-commands`."
        ),
    )
    vs.add_argument(
        "node_id",
        help="Node id to verify (e.g. cellular_categories.sheaves_cosheaves)",
    )
    vs.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    vs.add_argument(
        "--project", default=".",
        help="Blueprint project root (must contain docs/knowledge/).",
    )
    vs.add_argument("--timeout", type=int, default=300)
    vs.add_argument(
        "--no-include-staged", dest="include_staged_context",
        action="store_false",
        help="Limit context to admitted nodes only.",
    )
    vs.add_argument(
        "--no-write", action="store_true",
        help="Print the verdict but don't persist a review file.",
    )
    vs.set_defaults(include_staged_context=True, handler=_cmd_verify_stmt)


def _cmd_verify_stmt(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.project)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    # Imported here to avoid a circular import at module load time.
    from .main import _register_backends
    _register_backends()

    try:
        backend = get_backend(ns.backend)
    except BackendError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

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

    print(json.dumps(_review_for_stdout(review), indent=2, ensure_ascii=False))
    return EXIT_OK if review.is_accepted else EXIT_REVIEW_FAIL


# ---------------------------------------------------------------------------
# Review-payload shapers — public so primitives.py / tests can reuse
# ---------------------------------------------------------------------------
def _review_for_stdout(review) -> dict:
    """Verdict-only view — no body, no raw. Pipeable."""
    d = asdict(review)
    d.pop("raw", None)
    return d


def _review_for_disk(review) -> dict:
    """Disk view — verdict in frontmatter, raw LLM output in body."""
    d = asdict(review)
    raw = d.pop("raw", "") or ""
    d["body"] = (
        f"## Rationale\n\n{review.rationale}\n\n"
        f"## Raw LLM output\n\n```\n{raw}\n```\n"
    )
    return d
