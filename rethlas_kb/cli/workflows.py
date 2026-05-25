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
from rethlas_kb_agents.counterexample_hunter import (
    CounterexampleHunter,
    CounterexampleHuntReviewParseError,
)
from rethlas_kb_agents.def_stub_generator import (
    DefStubGenerator,
    DefStubReviewParseError,
)
from rethlas_kb_agents.proof_gap_filler import (
    GapFiller,
    GapFillReviewParseError,
)
from rethlas_kb_agents.proof_verifier import (
    ProofReviewParseError,
    ProofVerifier,
)
from rethlas_kb_agents.source_claim_verifier import (
    SourceClaimReviewParseError,
    SourceClaimVerifier,
)
from rethlas_kb_agents.statement_fixer import (
    StatementFixer,
    StatementFixReviewParseError,
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
from ._project_runner import BatchItem, BatchOutcome, run_batch
from rethlas_kb.project import ProjectError, load_project


def add_subparsers(sub) -> None:
    """Register every Mode B subcommand on a parent ``subparsers`` object."""
    _add_verify_stmt(sub)
    _add_verify_proof(sub)
    _add_fill_gap(sub)
    _add_hunt_counterexample(sub)
    _add_audit_source(sub)
    _add_fix_stmt(sub)
    _add_stub_def(sub)


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
        "node_id", nargs="?", default=None,
        help=(
            "Single-node mode: node id to verify "
            "(e.g. cellular_categories.sheaves_cosheaves). "
            "Omit when --project is given."
        ),
    )
    vs.add_argument(
        "--project", default=None,
        help=(
            "Batch mode: project id (loads "
            ".rethlas-kb/projects/<id>.yml). Runs the agent on every "
            "applicable closure member."
        ),
    )
    vs.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    vs.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
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
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    arg_err = _validate_node_or_project(ns)
    if arg_err:
        print(f"rethlas-kb: {arg_err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

    verifier = StatementVerifier(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    def _run_one(node) -> BatchItem:
        return _run_one_statement_verifier(verifier, node, adapter, ns)

    # ----- batch mode --------------------------------------------------
    if ns.project:
        project, exit_code = _load_project_or_exit(ns, adapter)
        if project is None:
            return exit_code  # type: ignore[return-value]
        outcome = run_batch(
            project, adapter, "statement-verifier", per_node=_run_one,
        )
        return _emit_batch_summary(outcome)

    # ----- single-node mode --------------------------------------------
    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    item = _run_one(node)
    if item.outcome == "crashed":
        # Keep the historical wording so external scripts grep on it.
        if "parse error" in (item.summary or ""):
            print(
                f"rethlas-kb: backend output could not be parsed: {item.error}",
                file=sys.stderr,
            )
        else:
            print(f"rethlas-kb: {item.error}", file=sys.stderr)
        return EXIT_RUNTIME

    # Single-node retains the rich verdict-on-stdout shape from v1
    # (the review object itself, not just the BatchItem summary).
    return _emit_single_review_outcome(item)


def _run_one_statement_verifier(verifier, node, adapter, ns) -> BatchItem:
    try:
        review = verifier.run(node.id, adapter)
    except StatementReviewParseError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"parse error: {exc.reason}",
            error=str(exc),
        )
    except BackendError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"backend error: {exc}",
            error=str(exc),
        )

    review_path = None
    if not ns.no_write:
        review_path = str(adapter.write_review(
            node_id=node.id, agent_name="statement-verifier",
            review=_review_for_disk(review),
        ))

    item = BatchItem(
        node_id=node.id,
        outcome="accepted" if review.is_accepted else "flagged",
        summary=f"{review.decision}: {review.rationale[:80]}",
        review_path=review_path,
    )
    # Cache the review for single-node rich-output mode
    item.__dict__["_review"] = review
    item.__dict__["_stdout_dict"] = _review_for_stdout(review)
    return item


def _emit_single_review_outcome(item: BatchItem) -> int:
    """For single-node mode: print the cached rich review JSON to stdout."""
    review = item.__dict__.get("_review")
    stdout_dict = item.__dict__.get("_stdout_dict")
    if item.review_path:
        print(f"review written: {item.review_path}", file=sys.stderr)
    if stdout_dict is not None:
        print(json.dumps(stdout_dict, indent=2, ensure_ascii=False))
    else:
        # Fallback for items that don't carry a cached review
        print(json.dumps({"outcome": item.outcome, "summary": item.summary},
                         indent=2, ensure_ascii=False))
    if item.outcome == "accepted":
        return EXIT_OK
    return EXIT_REVIEW_FAIL


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
        "node_id", nargs="?", default=None,
        help=(
            "Single-node mode: node id to verify "
            "(e.g. equivariant_sheaves.qfd_orbit_lemma). "
            "Omit when --project is given."
        ),
    )
    vp.add_argument(
        "--project", default=None,
        help="Batch mode: project id (runs on every applicable closure member).",
    )
    vp.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    vp.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
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
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    arg_err = _validate_node_or_project(ns)
    if arg_err:
        print(f"rethlas-kb: {arg_err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

    verifier = ProofVerifier(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    def _run_one(node) -> BatchItem:
        return _run_one_proof_verifier(verifier, node, adapter, ns)

    if ns.project:
        project, exit_code = _load_project_or_exit(ns, adapter)
        if project is None:
            return exit_code  # type: ignore[return-value]
        outcome = run_batch(
            project, adapter, "proof-verifier", per_node=_run_one,
        )
        return _emit_batch_summary(outcome)

    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    item = _run_one(node)
    if item.outcome == "crashed":
        if "parse error" in (item.summary or ""):
            print(
                f"rethlas-kb: backend output could not be parsed: {item.error}",
                file=sys.stderr,
            )
        else:
            print(f"rethlas-kb: {item.error}", file=sys.stderr)
        return EXIT_RUNTIME
    return _emit_single_review_outcome(item)


def _run_one_proof_verifier(verifier, node, adapter, ns) -> BatchItem:
    try:
        review = verifier.run(node.id, adapter, depth=ns.depth)
    except ProofReviewParseError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"parse error: {exc.reason}", error=str(exc),
        )
    except BackendError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"backend error: {exc}", error=str(exc),
        )

    review_path = None
    if not ns.no_write:
        review_path = str(adapter.write_review(
            node_id=node.id, agent_name="proof-verifier",
            review=_proof_review_for_disk(review),
        ))

    item = BatchItem(
        node_id=node.id,
        outcome="accepted" if review.is_accepted else "flagged",
        summary=(
            f"{review.final_verdict} (decisive: {review.decisive_stage}): "
            f"{review.rationale[:80]}"
        ),
        review_path=review_path,
    )
    item.__dict__["_stdout_dict"] = _proof_review_for_stdout(review)
    return item


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
        "node_id", nargs="?", default=None,
        help=(
            "Single-node mode: staged node id (e.g. algebra.lagrange). "
            "Omit when --project is given."
        ),
    )
    fg.add_argument(
        "--project", default=None,
        help=(
            "Batch mode: project id (runs on every applicable closure member; "
            "auto-discovers the most recent proof-verifier review per node "
            "via reviews/<slug>__proof-verifier*.md)."
        ),
    )
    fg.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    fg.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
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
    fg.add_argument(
        "--allow-same-backend", action="store_true",
        help=(
            "Suppress the cross-backend-isolation check (issue #13). "
            "Recommended only for single-machine debugging when only one "
            "CLI is installed; production runs should use different "
            "backends for proof-gap-filler vs proof-verifier."
        ),
    )
    fg.set_defaults(include_staged_context=True, handler=_cmd_fill_gap)


def _cmd_fill_gap(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    arg_err = _validate_node_or_project(ns)
    if arg_err:
        print(f"rethlas-kb: {arg_err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

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

    def _run_one(node) -> BatchItem:
        # In batch mode, auto-discover the latest proof-verifier review
        # for this node (override --prior-review since it can't apply
        # across many nodes).
        if ns.project:
            per_node_prior = _autoload_prior_review(adapter, node.id)
        else:
            per_node_prior = prior_review
        return _run_one_gap_filler(
            filler, node, adapter, ns,
            prior_review_text=per_node_prior,
        )

    if ns.project:
        project, exit_code = _load_project_or_exit(ns, adapter)
        if project is None:
            return exit_code  # type: ignore[return-value]
        outcome = run_batch(
            project, adapter, "proof-gap-filler", per_node=_run_one,
        )
        return _emit_batch_summary(outcome)

    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    item = _run_one(node)
    if item.outcome == "crashed":
        if "parse error" in (item.summary or ""):
            print(
                f"rethlas-kb: backend output could not be parsed: {item.error}",
                file=sys.stderr,
            )
        else:
            print(f"rethlas-kb: {item.error}", file=sys.stderr)
        return EXIT_RUNTIME
    return _emit_single_review_outcome(item)


def _autoload_prior_review(adapter, node_id: str) -> str:
    """Find the most recent proof-verifier review for a node; return its body or ''."""
    slug = node_id.replace(".", "_")
    candidates = sorted(
        adapter.reviews_dir.glob(f"{slug}__proof-verifier*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if adapter.reviews_dir.exists() else []
    if not candidates:
        return ""
    return candidates[0].read_text(encoding="utf-8")


def _run_one_gap_filler(filler, node, adapter, ns, *, prior_review_text: str) -> BatchItem:
    try:
        review = filler.run(
            node.id, adapter,
            prior_verification_report=prior_review_text,
            repair_count=ns.repair_count,
        )
    except GapFillReviewParseError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"parse error: {exc.reason}", error=str(exc),
        )
    except BackendError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"backend error: {exc}", error=str(exc),
        )

    applied: dict[str, list[str]] = {"updated_node": [], "requests": []}
    if not ns.no_apply and review.writes_proof:
        try:
            updated_path = adapter.update_staged_node_body(
                node.id, review.filled_proof,
            )
            applied["updated_node"].append(str(updated_path))
        except (KeyError, ValueError) as exc:
            print(
                f"rethlas-kb: gap-filler returned a proof for {node.id} but "
                f"update_staged_node_body failed: {exc}",
                file=sys.stderr,
            )
    if not ns.no_apply and review.new_sublemmas:
        for sublemma in review.new_sublemmas:
            try:
                req_path = adapter.write_request(
                    node_id=node.id,
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
                    f"rethlas-kb: failed to persist new-lemma request for "
                    f"{node.id} → {sublemma.id!r}: {exc}",
                    file=sys.stderr,
                )

    review_path = None
    if not ns.no_write_review:
        try:
            review_path = str(adapter.write_review(
                node_id=node.id, agent_name="proof-gap-filler",
                review=_gap_fill_review_for_disk(review, applied=applied),
            ))
        except Exception as exc:
            print(f"rethlas-kb: failed to write review for {node.id}: {exc}",
                  file=sys.stderr)

    item = BatchItem(
        node_id=node.id,
        outcome="accepted" if review.is_filled else "flagged",
        summary=f"{review.decision}: {review.rationale[:80]}",
        review_path=review_path,
    )
    item.__dict__["_stdout_dict"] = _gap_fill_review_for_stdout(
        review, applied=applied,
    )
    return item


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


# ---------------------------------------------------------------------------
# hunt-counterexample (Mode B, second generator)
# ---------------------------------------------------------------------------
def _add_hunt_counterexample(sub) -> None:
    hc = sub.add_parser(
        "hunt-counterexample",
        help=(
            "(Mode B) Counterexample-hunter: actively try to refute a "
            "stated claim by finding a concrete witness."
        ),
        description=(
            "Runs the counterexample-hunter agent on one node. This is "
            "INVERSE search — the agent reports whether a witness was "
            "found, never concludes 'the claim is therefore true' (that "
            "is proof-verifier's job)."
        ),
    )
    hc.add_argument(
        "node_id", nargs="?", default=None,
        help="Single-node mode. Omit when --project is given.",
    )
    hc.add_argument(
        "--project", default=None,
        help="Batch mode: project id (runs on every applicable closure member).",
    )
    hc.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    hc.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
    )
    hc.add_argument("--timeout", type=int, default=900)
    hc.add_argument(
        "--no-include-staged", dest="include_staged_context",
        action="store_false",
        help="Limit context to admitted nodes only.",
    )
    hc.add_argument(
        "--no-write", action="store_true",
        help="Print the verdict but don't persist a review file.",
    )
    hc.set_defaults(include_staged_context=True,
                    handler=_cmd_hunt_counterexample)


def _cmd_hunt_counterexample(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    arg_err = _validate_node_or_project(ns)
    if arg_err:
        print(f"rethlas-kb: {arg_err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

    hunter = CounterexampleHunter(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    def _run_one(node) -> BatchItem:
        return _run_one_counterexample_hunter(hunter, node, adapter, ns)

    if ns.project:
        project, exit_code = _load_project_or_exit(ns, adapter)
        if project is None:
            return exit_code  # type: ignore[return-value]
        outcome = run_batch(
            project, adapter, "counterexample-hunter", per_node=_run_one,
        )
        return _emit_batch_summary(outcome)

    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    item = _run_one(node)
    if item.outcome == "crashed":
        if "parse error" in (item.summary or ""):
            print(
                f"rethlas-kb: backend output could not be parsed: {item.error}",
                file=sys.stderr,
            )
        else:
            print(f"rethlas-kb: {item.error}", file=sys.stderr)
        return EXIT_RUNTIME
    return _emit_single_review_outcome(item)


def _run_one_counterexample_hunter(hunter, node, adapter, ns) -> BatchItem:
    try:
        review = hunter.run(node.id, adapter)
    except CounterexampleHuntReviewParseError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"parse error: {exc.reason}", error=str(exc),
        )
    except BackendError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"backend error: {exc}", error=str(exc),
        )

    review_path = None
    if not ns.no_write:
        review_path = str(adapter.write_review(
            node_id=node.id, agent_name="counterexample-hunter",
            review=_hunter_review_for_disk(review),
        ))

    # For the hunter: no_counterexample_found = "clean" = accepted-equivalent.
    # counterexample_found OR inconclusive = "flagged" (user must look).
    is_clean = review.decision == "no_counterexample_found"
    item = BatchItem(
        node_id=node.id,
        outcome="accepted" if is_clean else "flagged",
        summary=f"{review.decision}: {review.rationale[:80]}",
        review_path=review_path,
    )
    item.__dict__["_stdout_dict"] = _hunter_review_for_stdout(review)
    return item


def _hunter_review_for_stdout(review) -> dict:
    return {
        "decision": review.decision,
        "rationale": review.rationale,
        "confidence": review.confidence,
        "witness": (
            {"description": review.witness.description,
             "instantiation": review.witness.instantiation,
             "verification": review.witness.verification}
            if review.witness else None
        ),
        "suggested_fixes": list(review.suggested_fixes),
        "attempted_cases": [
            {"description": c.description, "outcome": c.outcome}
            for c in review.attempted_cases
        ],
        "why_inconclusive": review.why_inconclusive,
    }


def _hunter_review_for_disk(review) -> dict:
    body_parts = [f"## Rationale\n\n{review.rationale}\n"]
    if review.witness:
        body_parts.append(
            "## Witness (counterexample)\n\n"
            f"**Description:** {review.witness.description}\n\n"
            f"**Instantiation:** {review.witness.instantiation}\n\n"
            f"**Verification:** {review.witness.verification}\n"
        )
    if review.suggested_fixes:
        body_parts.append(
            "## Suggested fixes\n\n"
            + "\n".join(f"- {f}" for f in review.suggested_fixes) + "\n"
        )
    if review.attempted_cases:
        body_parts.append("## Attempted cases (search transparency)\n")
        for c in review.attempted_cases:
            body_parts.append(f"- **{c.description}** — {c.outcome}")
        body_parts.append("")
    if review.why_inconclusive:
        body_parts.append(
            f"## Why inconclusive\n\n{review.why_inconclusive}\n"
        )
    body_parts.append(f"## Raw LLM output\n\n```\n{review.raw}\n```\n")
    return {
        "decision": review.decision,
        "confidence": review.confidence,
        "has_witness": review.witness is not None,
        "n_attempted_cases": len(review.attempted_cases),
        "body": "\n".join(body_parts),
    }


# ---------------------------------------------------------------------------
# audit-source (Mode B, source-claim-verifier)
# ---------------------------------------------------------------------------
def _add_audit_source(sub) -> None:
    asp = sub.add_parser(
        "audit-source",
        help=(
            "(Mode B) Source-claim-verifier: audit an external-theorem "
            "node against the cited paper."
        ),
        description=(
            "Verifies alignment between an external-theorem node and a "
            "pre-extracted source passage from the cited paper. v1 takes "
            "the passage as input; PDF extraction is deferred to v1.5+."
        ),
    )
    asp.add_argument(
        "node_id", nargs="?", default=None,
        help="Single-node mode. Omit when --project is given.",
    )
    asp.add_argument(
        "--project", default=None,
        help=(
            "Batch mode: project id (runs on every external-theorem in the "
            "closure; --source-passage / --source-proof are ignored — agent "
            "returns cannot_verify for nodes lacking pre-staged source text)."
        ),
    )
    asp.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"Which LLM backend to use (default: {DEFAULT_BACKEND}).",
    )
    asp.add_argument(
        "--blueprint", default=".",
        help="Blueprint root (must contain docs/knowledge/).",
    )
    asp.add_argument(
        "--source-passage", default=None, metavar="PATH",
        help=(
            "Path to pre-extracted source-paper statement (or '-' for stdin). "
            "Without this, the agent will return cannot_verify."
        ),
    )
    asp.add_argument(
        "--source-proof", default=None, metavar="PATH",
        help=(
            "Optional path to pre-extracted source-paper proof "
            "(or '-' for stdin). When omitted, only alignment is checked."
        ),
    )
    asp.add_argument("--timeout", type=int, default=600)
    asp.add_argument(
        "--no-include-staged", dest="include_staged_context",
        action="store_false",
        help="Limit context to admitted nodes only.",
    )
    asp.add_argument(
        "--no-write", action="store_true",
        help="Print the verdict but don't persist a review file.",
    )
    asp.set_defaults(include_staged_context=True, handler=_cmd_audit_source)


def _cmd_audit_source(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    arg_err = _validate_node_or_project(ns)
    if arg_err:
        print(f"rethlas-kb: {arg_err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

    source_passage = ""
    if ns.source_passage:
        try:
            source_passage = read_file_or_stdin(ns.source_passage)
        except OSError as exc:
            print(f"rethlas-kb: --source-passage unreadable: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE

    source_proof = ""
    if ns.source_proof:
        try:
            source_proof = read_file_or_stdin(ns.source_proof)
        except OSError as exc:
            print(f"rethlas-kb: --source-proof unreadable: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE
    if ns.source_passage and ns.source_proof and ns.source_passage == "-" and ns.source_proof == "-":
        print(
            "rethlas-kb: only one of --source-passage / --source-proof "
            "can read from stdin in the same invocation",
            file=sys.stderr,
        )
        return EXIT_USAGE

    auditor = SourceClaimVerifier(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    def _run_one(node) -> BatchItem:
        # In batch mode, source-passage flags don't translate; pass ""
        # so the agent emits cannot_verify for nodes without
        # pre-staged source text. Single-node uses the flag values.
        if ns.project:
            return _run_one_source_claim_verifier(
                auditor, node, adapter, ns,
                source_passage="", source_proof="",
            )
        return _run_one_source_claim_verifier(
            auditor, node, adapter, ns,
            source_passage=source_passage, source_proof=source_proof,
        )

    if ns.project:
        project, exit_code = _load_project_or_exit(ns, adapter)
        if project is None:
            return exit_code  # type: ignore[return-value]
        outcome = run_batch(
            project, adapter, "source-claim-verifier", per_node=_run_one,
        )
        return _emit_batch_summary(outcome)

    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    item = _run_one(node)
    if item.outcome == "crashed":
        if "parse error" in (item.summary or ""):
            print(
                f"rethlas-kb: backend output could not be parsed: {item.error}",
                file=sys.stderr,
            )
        else:
            print(f"rethlas-kb: {item.error}", file=sys.stderr)
        return EXIT_RUNTIME
    return _emit_single_review_outcome(item)


def _run_one_source_claim_verifier(
    auditor, node, adapter, ns, *, source_passage: str, source_proof: str,
) -> BatchItem:
    try:
        review = auditor.run(
            node.id, adapter,
            source_passage=source_passage, source_proof=source_proof,
        )
    except SourceClaimReviewParseError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"parse error: {exc.reason}", error=str(exc),
        )
    except BackendError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"backend error: {exc}", error=str(exc),
        )

    review_path = None
    if not ns.no_write:
        review_path = str(adapter.write_review(
            node_id=node.id, agent_name="source-claim-verifier",
            review=_source_claim_review_for_disk(review),
        ))

    item = BatchItem(
        node_id=node.id,
        outcome="accepted" if review.is_accepted else "flagged",
        summary=f"{review.decision}: {review.rationale[:80]}",
        review_path=review_path,
    )
    item.__dict__["_stdout_dict"] = _source_claim_review_for_stdout(review)
    return item


def _source_claim_review_for_stdout(review) -> dict:
    return {
        "decision": review.decision,
        "rationale": review.rationale,
        "confidence": review.confidence,
        "quoted_node_statement": review.quoted_node_statement,
        "quoted_source_statement": review.quoted_source_statement,
        "differences": list(review.differences),
        "proof_issues": list(review.proof_issues),
        "missing_evidence": review.missing_evidence,
    }


def _source_claim_review_for_disk(review) -> dict:
    body_parts = [f"## Rationale\n\n{review.rationale}\n"]
    if review.quoted_node_statement:
        body_parts.append(
            f"## Node statement (verbatim)\n\n{review.quoted_node_statement}\n"
        )
    if review.quoted_source_statement:
        body_parts.append(
            f"## Source statement (verbatim)\n\n{review.quoted_source_statement}\n"
        )
    if review.differences:
        body_parts.append(
            "## Differences\n\n"
            + "\n".join(f"- {d}" for d in review.differences) + "\n"
        )
    if review.proof_issues:
        body_parts.append(
            "## Proof issues\n\n"
            + "\n".join(f"- {i}" for i in review.proof_issues) + "\n"
        )
    if review.missing_evidence:
        body_parts.append(
            f"## Missing evidence\n\n{review.missing_evidence}\n"
        )
    body_parts.append(f"## Raw LLM output\n\n```\n{review.raw}\n```\n")
    return {
        "decision": review.decision,
        "confidence": review.confidence,
        "n_differences": len(review.differences),
        "n_proof_issues": len(review.proof_issues),
        "body": "\n".join(body_parts),
    }


# ===========================================================================
# Shared batch-dispatch helpers (issue #15)
# ===========================================================================
def _validate_node_or_project(ns: argparse.Namespace) -> str | None:
    """Return None when args are OK; otherwise an error message."""
    has_node = bool(getattr(ns, "node_id", None))
    has_project = bool(getattr(ns, "project", None))
    if has_node and has_project:
        return (
            "pass either a positional node_id OR --project <id>, not both"
        )
    if not has_node and not has_project:
        return (
            "must pass either a positional node_id OR --project <id>"
        )
    return None


def _load_project_or_exit(
    ns: argparse.Namespace, adapter,
) -> tuple[object | None, int | None]:
    """Load the project manifest; return (project, None) or (None, exit_code)."""
    try:
        project = load_project(ns.project, adapter.kb_root)
    except ProjectError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return None, EXIT_USAGE
    return project, None


def _emit_batch_summary(outcome: BatchOutcome) -> int:
    """Emit a per-project summary and return the aggregate exit code.

    Exit codes:
      0 = every applicable node accepted
      1 = at least one node flagged (no crashes)
      3 = at least one node crashed during the agent invocation
    """
    print(
        f"\nbatch: project={outcome.project_id} agent={outcome.agent_role} "
        f"closure={outcome.closure_count} applicable={outcome.applicable_count} "
        f"accepted={outcome.accepted_count} flagged={outcome.flagged_count} "
        f"crashed={outcome.crashed_count}",
        file=sys.stderr,
    )
    for item in outcome.items:
        symbol = {"accepted": "✓", "flagged": "⚠", "crashed": "✗",
                  "skipped": "·"}.get(item.outcome, "?")
        print(
            f"  {symbol} [{item.outcome:8}] {item.node_id}  {item.summary}",
            file=sys.stderr,
        )
    print(
        json.dumps(_batch_outcome_to_dict(outcome), indent=2, ensure_ascii=False)
    )
    if outcome.crashed_count > 0:
        return EXIT_RUNTIME
    if outcome.flagged_count > 0:
        return EXIT_REVIEW_FAIL
    return EXIT_OK


def _batch_outcome_to_dict(outcome: BatchOutcome) -> dict:
    return {
        "project_id": outcome.project_id,
        "agent_role": outcome.agent_role,
        "closure_count": outcome.closure_count,
        "applicable_count": outcome.applicable_count,
        "accepted_count": outcome.accepted_count,
        "flagged_count": outcome.flagged_count,
        "crashed_count": outcome.crashed_count,
        "items": [
            {
                "node_id": i.node_id,
                "outcome": i.outcome,
                "summary": i.summary,
                "review_path": i.review_path,
                "error": i.error,
            }
            for i in outcome.items
        ],
    }


def _resolve_backend_or_exit(ns: argparse.Namespace) -> tuple[object | None, int | None]:
    """Boilerplate: register defaults, look up the backend, surface errors."""
    from .main import _register_backends
    _register_backends()
    try:
        return get_backend(ns.backend), None
    except BackendError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return None, EXIT_RUNTIME


# ===========================================================================
# fix-stmt (v1.4 — statement-fixer)
# ===========================================================================
def _add_fix_stmt(sub) -> None:
    fs = sub.add_parser(
        "fix-stmt",
        help=(
            "(Mode B) Statement-fixer: take a node + prior statement-verifier "
            "review, produce a corrected statement body."
        ),
        description=(
            "Generator that addresses formulation_issue / generality_concern / "
            "context_insufficient issues from a prior statement-verifier "
            "review. When decision=fixed, the staged node's body is "
            "rewritten in place (unless --no-apply)."
        ),
    )
    fs.add_argument(
        "node_id", nargs="?", default=None,
        help="Single-node mode: target node id. Omit when --project is given.",
    )
    fs.add_argument(
        "--project", default=None,
        help="Batch mode: project id (auto-discovers prior statement-verifier review per node).",
    )
    fs.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"LLM backend (default: {DEFAULT_BACKEND}).",
    )
    fs.add_argument("--blueprint", default=".")
    fs.add_argument(
        "--prior-review", default=None, metavar="PATH",
        help="Path to prior statement-verifier review (or '-' for stdin).",
    )
    fs.add_argument("--timeout", type=int, default=600)
    fs.add_argument("--no-include-staged", dest="include_staged_context",
                    action="store_false")
    fs.add_argument("--no-apply", action="store_true",
                    help="Don't apply fixed_body to the staged node.")
    fs.add_argument("--no-write-review", action="store_true")
    fs.set_defaults(include_staged_context=True, handler=_cmd_fix_stmt)


def _autoload_prior_statement_review(adapter, node_id: str) -> str:
    """Find the most recent statement-verifier review body, or ''."""
    slug = node_id.replace(".", "_")
    if not adapter.reviews_dir.exists():
        return ""
    candidates = sorted(
        adapter.reviews_dir.glob(f"{slug}__statement-verifier*.md"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return candidates[0].read_text(encoding="utf-8") if candidates else ""


def _cmd_fix_stmt(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    arg_err = _validate_node_or_project(ns)
    if arg_err:
        print(f"rethlas-kb: {arg_err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

    explicit_prior = ""
    if ns.prior_review:
        try:
            explicit_prior = read_file_or_stdin(ns.prior_review)
        except OSError as exc:
            print(f"rethlas-kb: --prior-review unreadable: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE

    fixer = StatementFixer(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    def _run_one(node) -> BatchItem:
        per_node_prior = (
            _autoload_prior_statement_review(adapter, node.id)
            if ns.project else explicit_prior
        )
        return _run_one_statement_fixer(
            fixer, node, adapter, ns, prior_review_text=per_node_prior,
        )

    if ns.project:
        project, exit_code = _load_project_or_exit(ns, adapter)
        if project is None:
            return exit_code  # type: ignore[return-value]
        outcome = run_batch(
            project, adapter, "statement-fixer", per_node=_run_one,
        )
        return _emit_batch_summary(outcome)

    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    item = _run_one(node)
    if item.outcome == "crashed":
        if "parse error" in (item.summary or ""):
            print(
                f"rethlas-kb: backend output could not be parsed: {item.error}",
                file=sys.stderr,
            )
        else:
            print(f"rethlas-kb: {item.error}", file=sys.stderr)
        return EXIT_RUNTIME
    return _emit_single_review_outcome(item)


def _run_one_statement_fixer(
    fixer, node, adapter, ns, *, prior_review_text: str,
) -> BatchItem:
    try:
        review = fixer.run(
            node.id, adapter, prior_review=prior_review_text,
        )
    except StatementFixReviewParseError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"parse error: {exc.reason}", error=str(exc),
        )
    except BackendError as exc:
        return BatchItem(
            node_id=node.id, outcome="crashed",
            summary=f"backend error: {exc}", error=str(exc),
        )

    applied: list[str] = []
    if not ns.no_apply and review.writes_body:
        try:
            updated = adapter.update_staged_node_body(node.id, review.fixed_body)
            applied.append(str(updated))
        except (KeyError, ValueError) as exc:
            print(
                f"rethlas-kb: fixer returned a body for {node.id} but "
                f"update_staged_node_body failed: {exc}",
                file=sys.stderr,
            )

    review_path = None
    if not ns.no_write_review:
        try:
            review_path = str(adapter.write_review(
                node_id=node.id, agent_name="statement-fixer",
                review={
                    "decision": review.decision,
                    "rationale": review.rationale,
                    "confidence": review.confidence,
                    "addressed_issues": list(review.addressed_issues),
                    "blocker": review.blocker,
                    "applied_updated_node": applied,
                    "body": (
                        f"## Rationale\n\n{review.rationale}\n\n"
                        + (f"## Addressed issues\n\n"
                           + "\n".join(f"- {i}" for i in review.addressed_issues)
                           + "\n\n" if review.addressed_issues else "")
                        + (f"## Blocker\n\n{review.blocker}\n\n"
                           if review.blocker else "")
                        + (f"## Fixed body (applied to staged node)\n\n"
                           + review.fixed_body + "\n\n"
                           if review.fixed_body else "")
                        + f"## Raw LLM output\n\n```\n{review.raw}\n```\n"
                    ),
                },
            ))
        except Exception as exc:
            print(f"rethlas-kb: failed to write review for {node.id}: {exc}",
                  file=sys.stderr)

    item = BatchItem(
        node_id=node.id,
        outcome="accepted" if review.decision == "fixed" else "flagged",
        summary=f"{review.decision}: {review.rationale[:80]}",
        review_path=review_path,
    )
    item.__dict__["_stdout_dict"] = {
        "decision": review.decision,
        "rationale": review.rationale,
        "confidence": review.confidence,
        "addressed_issues": list(review.addressed_issues),
        "blocker": review.blocker,
        "applied_updated_node": applied,
    }
    return item


# ===========================================================================
# stub-def (v1.4 — def-stub-generator)
# ===========================================================================
def _add_stub_def(sub) -> None:
    sd = sub.add_parser(
        "stub-def",
        help=(
            "(Mode B) Def-stub-generator: create a staged definition stub "
            "for a missing-definition gap."
        ),
        description=(
            "Generator that creates a staged stub for a missing-definition "
            "name flagged by statement-verifier. Writes either a real "
            "first-pass body (drafted) or a TODO placeholder "
            "(placeholder_only) via KbAdapter.write_staged_node."
        ),
    )
    sd.add_argument(
        "missing_id",
        help="Proposed id for the new definition (e.g. algebra.normal_subgroup).",
    )
    sd.add_argument(
        "--referring-node", required=True,
        help="Node id that flagged this term as needs_definition.",
    )
    sd.add_argument(
        "--reason", default="",
        help="Short note on what role the missing term plays in the referring node.",
    )
    sd.add_argument(
        "--backend", choices=("codex", "claude"), default=DEFAULT_BACKEND,
        help=f"LLM backend (default: {DEFAULT_BACKEND}).",
    )
    sd.add_argument("--blueprint", default=".")
    sd.add_argument("--timeout", type=int, default=300)
    sd.add_argument("--no-include-staged", dest="include_staged_context",
                    action="store_false")
    sd.add_argument("--no-apply", action="store_true",
                    help="Don't write the stub; just emit the verdict.")
    sd.set_defaults(include_staged_context=True, handler=_cmd_stub_def)


def _cmd_stub_def(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    backend, exit_code = _resolve_backend_or_exit(ns)
    if backend is None:
        return exit_code  # type: ignore[return-value]

    stubber = DefStubGenerator(
        backend=backend,
        timeout_seconds=ns.timeout,
        include_staged_context=ns.include_staged_context,
    )

    try:
        review = stubber.run(
            ns.missing_id, ns.referring_node, adapter, reason=ns.reason,
        )
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except DefStubReviewParseError as exc:
        print(
            f"rethlas-kb: backend output could not be parsed: {exc}",
            file=sys.stderr,
        )
        return EXIT_RUNTIME
    except BackendError as exc:
        print(f"rethlas-kb: backend error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    applied = None
    if not ns.no_apply and review.writes_stub:
        try:
            staged_path = adapter.write_staged_node(
                frontmatter={
                    "id": review.proposed_id,
                    "title": review.title,
                    "kind": "definition",
                    "status": "staged",
                    "primary_topic": (
                        review.primary_topic
                        or review.proposed_id.split(".", 1)[0]
                    ),
                    "topics": (
                        list(review.topics) if review.topics
                        else [review.primary_topic
                              or review.proposed_id.split(".", 1)[0]]
                    ),
                    "uses": list(review.uses),
                },
                body=review.body,
            )
            applied = str(staged_path)
        except ValueError as exc:
            print(
                f"rethlas-kb: stubber returned a draft but "
                f"write_staged_node rejected it: {exc}",
                file=sys.stderr,
            )

    payload = {
        "decision": review.decision,
        "rationale": review.rationale,
        "confidence": review.confidence,
        "proposed_id": review.proposed_id,
        "title": review.title,
        "primary_topic": review.primary_topic,
        "topics": list(review.topics),
        "uses": list(review.uses),
        "blocker": review.blocker,
        "applied_staged_node": applied,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if review.decision == "cannot_stub":
        return EXIT_REVIEW_FAIL
    return EXIT_OK
