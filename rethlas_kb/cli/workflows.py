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
from rethlas_kb_agents.proof_gap_filler import (
    GapFiller,
    GapFillReviewParseError,
)
from rethlas_kb_agents.proof_verifier import (
    ProofReviewParseError,
    ProofVerifier,
)
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
from ._io import adapter_for, read_file_or_stdin


def add_subparsers(sub) -> None:
    """Register every Mode B subcommand on a parent ``subparsers`` object."""
    _add_verify_stmt(sub)
    _add_verify_proof(sub)
    _add_fill_gap(sub)


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


# ---------------------------------------------------------------------------
# verify-proof (Mode B, 3-stage with --depth)
# ---------------------------------------------------------------------------
def _add_verify_proof(sub) -> None:
    vp = sub.add_parser(
        "verify-proof",
        help=(
            "(Mode B) Proof-verifier: QED-style 3-stage pipeline with "
            "judge / structural / detailed."
        ),
        description=(
            "Run the proof-verifier agent against one node. The pipeline "
            "is depth-adaptive: judge classifies, and only escalates to "
            "structural + detailed when the proof is non-trivial. For "
            "Mode A use slash commands installed by `install-commands`."
        ),
    )
    vp.add_argument(
        "node_id",
        help="Node id to verify (e.g. equivariant_sheaves.qfd_orbit_lemma)",
    )
    vp.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    vp.add_argument(
        "--project", default=".",
        help="Blueprint project root (must contain docs/knowledge/).",
    )
    vp.add_argument(
        "--depth", choices=("auto", "easy", "structural", "detailed"),
        default="auto",
        help=(
            "auto = full pipeline with short-circuit (default). "
            "easy = only judge. structural = skip judge. detailed = "
            "skip judge AND structural; trust the caller has already "
            "verified structure."
        ),
    )
    vp.add_argument("--timeout", type=int, default=600)
    vp.add_argument(
        "--no-include-staged", dest="include_staged_context",
        action="store_false",
        help="Limit context to admitted nodes only.",
    )
    vp.add_argument(
        "--no-write", action="store_true",
        help="Print the verdict but don't persist a review file.",
    )
    vp.set_defaults(include_staged_context=True, handler=_cmd_verify_proof)


def _cmd_verify_proof(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.project)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    from .main import _register_backends
    _register_backends()

    try:
        backend = get_backend(ns.backend)
    except BackendError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    verifier = ProofVerifier(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    try:
        review = verifier.run(ns.node_id, adapter, depth=ns.depth)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except ProofReviewParseError as exc:
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
            agent_name="proof-verifier",
            review=_proof_review_for_disk(review),
        )
        print(f"review written: {review_path}", file=sys.stderr)

    print(json.dumps(_proof_review_for_stdout(review), indent=2, ensure_ascii=False))
    return EXIT_OK if review.is_accepted else EXIT_REVIEW_FAIL


def _proof_review_for_stdout(review) -> dict:
    """Compact verdict view — no raw per-stage dumps. Pipeable."""
    return {
        "final_verdict": review.final_verdict,
        "rationale": review.rationale,
        "depth_requested": review.depth_requested,
        "decisive_stage": review.decisive_stage,
        "short_circuited_at": review.short_circuited_at,
        "stages_run": review.stages_run,
        "judge_difficulty": (review.judge.difficulty if review.judge else None),
        "structural_verdict": (review.structural.verdict if review.structural else None),
        "detailed_verdict": (review.detailed.verdict if review.detailed else None),
    }


def _proof_review_for_disk(review) -> dict:
    """Disk view — final verdict in frontmatter, raw per-stage outputs in body."""
    body_parts: list[str] = [
        f"## Final verdict\n\n**{review.final_verdict}** "
        f"(decisive stage: {review.decisive_stage}; "
        f"short-circuit: {review.short_circuited_at or '—'})\n\n"
        f"{review.rationale}\n",
    ]
    for stage_name, stage in (
        ("Judge", review.judge),
        ("Structural", review.structural),
        ("Detailed", review.detailed),
    ):
        if stage is None:
            continue
        body_parts.append(
            f"## Stage: {stage_name} (raw LLM output)\n\n"
            f"```\n{getattr(stage, 'raw', '')}\n```\n"
        )
    return {
        "final_verdict": review.final_verdict,
        "depth_requested": review.depth_requested,
        "decisive_stage": review.decisive_stage,
        "short_circuited_at": review.short_circuited_at,
        "stages_run": review.stages_run,
        "judge_difficulty": (review.judge.difficulty if review.judge else None),
        "structural_verdict": (review.structural.verdict if review.structural else None),
        "detailed_verdict": (review.detailed.verdict if review.detailed else None),
        "body": "\n".join(body_parts),
    }


# ---------------------------------------------------------------------------
# fill-gap (Mode B, generator)
# ---------------------------------------------------------------------------
def _add_fill_gap(sub) -> None:
    fg = sub.add_parser(
        "fill-gap",
        help=(
            "(Mode B) Proof-gap-filler: produce a completed proof for a "
            "node with partial / missing proof."
        ),
        description=(
            "Runs the proof-gap-filler agent on one staged node. When the "
            "decision is filled or partial, the staged node's body is "
            "rewritten (unless --no-apply). New sub-lemmas are persisted "
            "under docs/knowledge/requests/."
        ),
    )
    fg.add_argument(
        "node_id",
        help="Staged node id (e.g. algebra.lagrange).",
    )
    fg.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    fg.add_argument(
        "--project", default=".",
        help="Blueprint project root (must contain docs/knowledge/).",
    )
    fg.add_argument(
        "--prior-review", default=None, metavar="PATH",
        help=(
            "Path to a prior proof-verifier review markdown (or '-' for "
            "stdin). Body becomes the verification feedback for repair mode."
        ),
    )
    fg.add_argument(
        "--repair-count", type=int, default=0,
        help=(
            "How many repair iterations have already occurred. "
            ">=2 triggers Phase II reroute (previous proof omitted)."
        ),
    )
    fg.add_argument("--timeout", type=int, default=900)
    fg.add_argument(
        "--no-include-staged", dest="include_staged_context",
        action="store_false",
        help="Limit context to admitted nodes only.",
    )
    fg.add_argument(
        "--no-apply", action="store_true",
        help=(
            "Skip applying the filled proof to the staged node. Just "
            "emit the review verdict on stdout. Useful for dry-run / "
            "review-before-apply workflows."
        ),
    )
    fg.add_argument(
        "--no-write-review", action="store_true",
        help="Don't persist a review file under docs/knowledge/reviews/.",
    )
    fg.set_defaults(include_staged_context=True, handler=_cmd_fill_gap)


def _cmd_fill_gap(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.project)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    from .main import _register_backends
    _register_backends()

    try:
        backend = get_backend(ns.backend)
    except BackendError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    prior_review = ""
    if ns.prior_review:
        try:
            prior_review = read_file_or_stdin(ns.prior_review)
        except OSError as exc:
            print(f"rethlas-kb: --prior-review unreadable: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE

    filler = GapFiller(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    try:
        review = filler.run(
            ns.node_id, adapter,
            prior_verification_report=prior_review,
            repair_count=ns.repair_count,
        )
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except GapFillReviewParseError as exc:
        print(
            f"rethlas-kb: backend output could not be parsed: {exc}",
            file=sys.stderr,
        )
        return EXIT_RUNTIME
    except BackendError as exc:
        print(f"rethlas-kb: backend error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    # Apply side-effects
    applied: dict[str, list[str]] = {"updated_node": [], "requests": []}
    if not ns.no_apply and review.writes_proof:
        try:
            updated_path = adapter.update_staged_node_body(
                ns.node_id, review.filled_proof,
            )
            applied["updated_node"].append(str(updated_path))
        except (KeyError, ValueError) as exc:
            print(
                f"rethlas-kb: gap-filler returned a proof but "
                f"update_staged_node_body failed: {exc}",
                file=sys.stderr,
            )
            # Keep going — we still want to record the review
    if not ns.no_apply and review.new_sublemmas:
        for sublemma in review.new_sublemmas:
            try:
                req_path = adapter.write_request(
                    node_id=ns.node_id,
                    request_kind="new-lemma",
                    payload={
                        "proposed_id": sublemma.id,
                        "statement": sublemma.statement,
                        "rationale": sublemma.rationale,
                        "body": (
                            f"## Proposed lemma\n\n**id**: `{sublemma.id}`\n\n"
                            f"**statement**: {sublemma.statement}\n\n"
                            f"**rationale**: {sublemma.rationale}\n"
                        ),
                    },
                )
                applied["requests"].append(str(req_path))
            except Exception as exc:
                print(
                    f"rethlas-kb: failed to persist new-lemma request "
                    f"for {sublemma.id!r}: {exc}",
                    file=sys.stderr,
                )

    if not ns.no_write_review:
        try:
            review_path = adapter.write_review(
                node_id=ns.node_id,
                agent_name="proof-gap-filler",
                review=_gap_fill_review_for_disk(review, applied=applied),
            )
            print(f"review written: {review_path}", file=sys.stderr)
        except Exception as exc:
            print(f"rethlas-kb: failed to write review: {exc}", file=sys.stderr)

    print(json.dumps(_gap_fill_review_for_stdout(review, applied=applied),
                     indent=2, ensure_ascii=False))
    return EXIT_OK if review.is_filled else EXIT_REVIEW_FAIL


def _gap_fill_review_for_stdout(review, *, applied: dict) -> dict:
    return {
        "decision": review.decision,
        "rationale": review.rationale,
        "confidence": review.confidence,
        "gap_remaining": review.gap_remaining,
        "suggested_approaches": list(review.suggested_approaches),
        "new_sublemmas": [
            {"id": s.id, "statement": s.statement, "rationale": s.rationale}
            for s in review.new_sublemmas
        ],
        "applied": applied,
    }


def _gap_fill_review_for_disk(review, *, applied: dict) -> dict:
    body_parts = [f"## Rationale\n\n{review.rationale}\n"]
    if review.gap_remaining:
        body_parts.append(f"## Gap remaining\n\n{review.gap_remaining}\n")
    if review.suggested_approaches:
        body_parts.append(
            "## Suggested approaches\n\n"
            + "\n".join(f"- {a}" for a in review.suggested_approaches)
            + "\n"
        )
    if review.new_sublemmas:
        body_parts.append("## New sub-lemmas requested\n")
        for s in review.new_sublemmas:
            body_parts.append(
                f"- **{s.id}**: {s.statement}"
                + (f" — {s.rationale}" if s.rationale else "")
            )
        body_parts.append("")
    if review.filled_proof:
        body_parts.append(
            "## Filled proof (also applied to staged node)\n\n"
            + review.filled_proof + "\n"
        )
    body_parts.append(f"## Raw LLM output\n\n```\n{review.raw}\n```\n")
    return {
        "decision": review.decision,
        "confidence": review.confidence,
        "applied_updated_node": applied.get("updated_node", []),
        "applied_requests": applied.get("requests", []),
        "body": "\n".join(body_parts),
    }
