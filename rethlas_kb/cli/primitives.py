"""Mode A primitives — KbAdapter operations exposed as CLI subcommands.

These are the tools that agentic CLIs (codex / claude / opencode) call
when orchestrating a verification workflow themselves. Each command is
a thin shell over one or two KbAdapter methods.

Design rules:

- Default output is **rendered text** (markdown / human-readable) so it
  flows straight into an LLM prompt.
- ``--json`` opt-in gives the LLM something machine-parseable when it
  needs to branch on specific fields.
- stdout for data, stderr for diagnostics — pipe-friendly.
- Every command accepts ``--project`` (defaults to cwd).
- Exit codes follow ``_constants``: 0 success, 2 bad args, 3 runtime.

Agent ↔ prompt mapping (extended as we add more agents in #9–12):

    statement-verifier → rethlas_kb_agents.statement_verifier.prompt.compose
    proof-verifier     → (added in #9)
    proof-gap-filler   → (added in #10)
    counterexample-hunter → (added in #11)
    source-claim-verifier → (added in #12)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from rethlas_kb_agents.counterexample_hunter.prompt import (
    compose as _compose_counterexample_hunter_prompt,
)
from rethlas_kb_agents.source_claim_verifier.prompt import (
    compose as _compose_source_claim_verifier_prompt,
)
from rethlas_kb_agents.proof_gap_filler.prompt import (
    compose as _compose_proof_gap_filler_prompt,
)
from rethlas_kb_agents.proof_verifier.prompts import (
    compose_detailed as _compose_proof_verifier_detailed_prompt,
    compose_judge as _compose_proof_verifier_judge_prompt,
    compose_structural as _compose_proof_verifier_structural_prompt,
)
from rethlas_kb_agents.statement_fixer.prompt import (
    compose as _compose_statement_fixer_prompt,
)
from rethlas_kb_agents.statement_verifier.prompt import (
    compose as _compose_statement_verifier_prompt,
)

from ._constants import EXIT_OK, EXIT_RUNTIME, EXIT_USAGE
from ._io import adapter_for, read_file_or_stdin, read_stdin_text, split_csv


# Agent role → prompt composer. Stage-aware roles (proof-verifier-*) are
# registered as separate keys so the slash commands stay simple.
_PROMPT_COMPOSERS = {
    "statement-verifier": _compose_statement_verifier_prompt,
    "proof-verifier-judge": _compose_proof_verifier_judge_prompt,
    "proof-verifier-structural": _compose_proof_verifier_structural_prompt,
    "proof-verifier-detailed": _compose_proof_verifier_detailed_prompt,
    "proof-gap-filler": _compose_proof_gap_filler_prompt,
    "counterexample-hunter": _compose_counterexample_hunter_prompt,
    "source-claim-verifier": _compose_source_claim_verifier_prompt,
    "statement-fixer": _compose_statement_fixer_prompt,
    # def-stub-generator uses a different compose() signature (missing_id +
    # referring_node + reason) — exposed via Mode B `stub-def` only, not
    # the generic compose-prompt primitive.
}


def add_subparsers(sub) -> None:
    """Register every Mode A primitive on a parent ``subparsers`` object."""
    # Reads
    _add_get_node(sub)
    _add_get_context(sub)
    _add_compose_prompt(sub)
    _add_list_staged(sub)
    _add_list_admitted(sub)
    # Writes
    _add_write_review(sub)
    _add_write_request(sub)
    _add_write_staged_node(sub)
    _add_update_staged_node_body(sub)
    _add_promote_request(sub)
    # Validate
    _add_validate_frontmatter(sub)


# ---------------------------------------------------------------------------
# get-node
# ---------------------------------------------------------------------------
def _add_get_node(sub) -> None:
    p = sub.add_parser(
        "get-node",
        help="Print one node's content (Mode A primitive).",
        description=(
            "Print the raw markdown file (default), the parsed node as "
            "JSON (--format json), or just the YAML frontmatter "
            "(--format frontmatter)."
        ),
    )
    p.add_argument("node_id")
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument(
        "--format", choices=("text", "json", "frontmatter"), default="text",
    )
    p.set_defaults(handler=_cmd_get_node)


def _cmd_get_node(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        node = adapter.read_node(ns.node_id)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if ns.format == "text":
        if node.file_path and Path(node.file_path).exists():
            sys.stdout.write(Path(node.file_path).read_text(encoding="utf-8"))
        else:
            sys.stdout.write(node.body)
    elif ns.format == "json":
        print(json.dumps(_node_to_json(node), indent=2,
                         ensure_ascii=False, default=str))
    elif ns.format == "frontmatter":
        sys.stdout.write(yaml.safe_dump(
            _node_to_frontmatter(node), sort_keys=False, allow_unicode=True,
        ))
    return EXIT_OK


def _node_to_json(node) -> dict:
    return {
        "id": node.id,
        "title": node.title,
        "kind": node.kind,
        "status": node.status,
        "uses": list(node.uses),
        "primary_topic": node.primary_topic,
        "topics": list(node.topics),
        "tags": list(node.tags),
        "body": node.body,
        "file_path": str(node.file_path) if node.file_path else None,
    }


def _node_to_frontmatter(node) -> dict:
    fm: dict = {
        "id": node.id,
        "title": node.title,
        "kind": node.kind,
        "status": node.status,
    }
    if node.uses:
        fm["uses"] = list(node.uses)
    if node.primary_topic:
        fm["primary_topic"] = node.primary_topic
    if node.topics:
        fm["topics"] = list(node.topics)
    if node.tags:
        fm["tags"] = list(node.tags)
    return fm


# ---------------------------------------------------------------------------
# get-context
# ---------------------------------------------------------------------------
def _add_get_context(sub) -> None:
    p = sub.add_parser(
        "get-context",
        help="Print the context pack (dependency closure) for one node.",
        description=(
            "Default output is rendered markdown ready to feed into an LLM. "
            "Pass --json for the raw dict that mdblueprint's "
            "build_context_pack returns."
        ),
    )
    p.add_argument("node_id")
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument(
        "--no-staged", dest="include_staged", action="store_false",
        help="Admitted-only context (default: include staged as non-admitted evidence).",
    )
    p.add_argument("--json", action="store_true",
                   help="Emit raw context_pack dict as JSON.")
    p.set_defaults(include_staged=True, handler=_cmd_get_context)


def _cmd_get_context(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    try:
        bundle = adapter.context_pack(
            target_id=ns.node_id, include_staged=ns.include_staged,
        )
    except (KeyError, ValueError) as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if ns.json:
        print(json.dumps(bundle.raw, indent=2, ensure_ascii=False, default=str))
    else:
        # Reuse the prompt helper's renderer so output matches what
        # compose-prompt produces in its context section.
        from rethlas_kb_agents.statement_verifier.prompt import _render_context
        sys.stdout.write(_render_context(bundle))
        sys.stdout.write("\n")
    return EXIT_OK


# ---------------------------------------------------------------------------
# compose-prompt
# ---------------------------------------------------------------------------
def _add_compose_prompt(sub) -> None:
    p = sub.add_parser(
        "compose-prompt",
        help="Print the ready-to-send LLM prompt for one (role, node) pair.",
        description=(
            "Emits the full prompt — system block + target node + context "
            "pack + output contract — that the named agent role would send "
            "to its backend. Slash commands invoke this to get the math "
            "reasoning prompt; they own the workflow instructions separately."
        ),
    )
    p.add_argument(
        "role",
        help="Agent role (e.g. statement-verifier).",
        choices=sorted(_PROMPT_COMPOSERS.keys()),
    )
    p.add_argument("node_id")
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument("--no-staged", dest="include_staged", action="store_false")
    p.set_defaults(include_staged=True, handler=_cmd_compose_prompt)


def _cmd_compose_prompt(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    composer = _PROMPT_COMPOSERS.get(ns.role)
    if composer is None:  # belt + suspenders — argparse already restricts choices
        print(f"rethlas-kb: unknown agent role {ns.role!r}", file=sys.stderr)
        return EXIT_USAGE
    try:
        node = adapter.read_node(ns.node_id)
        bundle = adapter.context_pack(
            target_id=ns.node_id, include_staged=ns.include_staged,
        )
    except (KeyError, ValueError) as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    # Project-rules sidecar (issue #22) — appended automatically; the
    # agentic CLI doesn't need to know it exists.
    # Multi-stage roles also inherit rules from their base role
    # (e.g. proof-verifier-judge picks up proof-verifier.md too).
    project_rules = adapter.read_project_rules_chain(*_role_chain(ns.role))
    sys.stdout.write(composer(node, bundle, project_rules=project_rules))
    return EXIT_OK


# Multi-stage roles inherit rules from their base role. Update this map
# whenever a new multi-stage agent lands.
_STAGE_BASE_ROLES: dict[str, str] = {
    "proof-verifier-judge": "proof-verifier",
    "proof-verifier-structural": "proof-verifier",
    "proof-verifier-detailed": "proof-verifier",
}


def _role_chain(role: str) -> tuple[str, ...]:
    """Order: base role first (broad), then specific stage role (narrow)."""
    base = _STAGE_BASE_ROLES.get(role)
    if base is not None and base != role:
        return (base, role)
    return (role,)


# ---------------------------------------------------------------------------
# list-staged / list-admitted
# ---------------------------------------------------------------------------
def _add_list_staged(sub) -> None:
    p = sub.add_parser(
        "list-staged",
        help="List staged nodes (Mode A primitive).",
    )
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument("--topic", default=None,
                   help="Filter to one topic id (e.g. cellular_categories).")
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_cmd_list_staged)


def _cmd_list_staged(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    nodes = (
        adapter.list_staged_by_topic(ns.topic) if ns.topic
        else adapter.list_staged()
    )
    _print_node_list(nodes, as_json=ns.json)
    return EXIT_OK


def _add_list_admitted(sub) -> None:
    p = sub.add_parser(
        "list-admitted",
        help="List admitted nodes (Mode A primitive).",
    )
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument("--topic", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=_cmd_list_admitted)


def _cmd_list_admitted(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    nodes = adapter.list_admitted()
    if ns.topic:
        from tools.knowledge.export import leaf_topic_ids_for_node
        nodes = [n for n in nodes if ns.topic in leaf_topic_ids_for_node(n)]
    _print_node_list(nodes, as_json=ns.json)
    return EXIT_OK


def _print_node_list(nodes, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(
            [{"id": n.id, "title": n.title, "kind": n.kind,
              "status": n.status, "primary_topic": n.primary_topic}
             for n in nodes],
            indent=2, ensure_ascii=False,
        ))
        return
    # Text shape: "<id>\t<kind>\t<status>\t<title>"
    if not nodes:
        return
    for n in nodes:
        print(f"{n.id}\t{n.kind}\t{n.status}\t{n.title}")


# ---------------------------------------------------------------------------
# write-review
# ---------------------------------------------------------------------------
def _add_write_review(sub) -> None:
    p = sub.add_parser(
        "write-review",
        help="Persist an agent review for a node (Mode A primitive).",
        description=(
            "Writes a markdown file under docs/knowledge/reviews/ with the "
            "verdict fields in YAML frontmatter and (optionally) the full "
            "LLM reasoning trace in the markdown body."
        ),
    )
    p.add_argument("node_id")
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument("--agent", required=True,
                   help="Agent role that produced the verdict.")
    p.add_argument("--decision", required=True,
                   help="Verdict token (agent-specific; e.g. accepted).")
    p.add_argument("--rationale", required=True,
                   help="One or two sentences justifying the verdict.")
    p.add_argument("--confidence", type=float, default=None,
                   help="Confidence in [0, 1].")
    p.add_argument("--missing-definitions", default=None,
                   help="Comma-separated node ids.")
    p.add_argument("--formulation-issues", default=None,
                   help="Comma-separated short descriptions.")
    p.add_argument("--generality-notes", default=None,
                   help="Free text on generality concerns.")
    p.add_argument(
        "--raw", default=None, metavar="PATH",
        help=(
            "Path to a file containing the full LLM reasoning trace, "
            "or '-' to read it from stdin. Ends up in the markdown body."
        ),
    )
    p.set_defaults(handler=_cmd_write_review)


def _cmd_write_review(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    raw = read_file_or_stdin(ns.raw) if ns.raw else ""

    review: dict = {
        "decision": ns.decision,
        "rationale": ns.rationale,
    }
    if ns.confidence is not None:
        review["confidence"] = ns.confidence
    md = split_csv(ns.missing_definitions)
    if md:
        review["missing_definitions"] = md
    fi = split_csv(ns.formulation_issues)
    if fi:
        review["formulation_issues"] = fi
    if ns.generality_notes:
        review["generality_notes"] = ns.generality_notes
    if raw:
        review["body"] = (
            f"## Rationale\n\n{ns.rationale}\n\n"
            f"## Raw LLM output\n\n```\n{raw}\n```\n"
        )

    try:
        path = adapter.write_review(
            node_id=ns.node_id, agent_name=ns.agent, review=review,
        )
    except Exception as exc:  # adapter raises ValueError / OSError
        print(f"rethlas-kb: write-review failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(path)
    return EXIT_OK


# ---------------------------------------------------------------------------
# write-request
# ---------------------------------------------------------------------------
def _add_write_request(sub) -> None:
    p = sub.add_parser(
        "write-request",
        help="Persist a request (e.g. missing-dependency) for a node.",
    )
    p.add_argument("node_id")
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument("--kind", required=True,
                   help="Request kind (missing-dependency / gap-fill / ...).")
    p.add_argument(
        "--payload", default=None, metavar="PATH",
        help="Path to a JSON file (or '-' for stdin) with the request payload.",
    )
    p.add_argument("--body", default=None, metavar="PATH",
                   help="Path to markdown body (or '-' for stdin).")
    p.set_defaults(handler=_cmd_write_request)


def _cmd_write_request(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    payload: dict = {}
    if ns.payload:
        try:
            payload = json.loads(read_file_or_stdin(ns.payload))
        except json.JSONDecodeError as exc:
            print(f"rethlas-kb: --payload not valid JSON: {exc}",
                  file=sys.stderr)
            return EXIT_USAGE
        if not isinstance(payload, dict):
            print("rethlas-kb: --payload JSON must be an object", file=sys.stderr)
            return EXIT_USAGE
    if ns.body:
        payload["body"] = read_file_or_stdin(ns.body)

    try:
        path = adapter.write_request(
            node_id=ns.node_id, request_kind=ns.kind, payload=payload,
        )
    except Exception as exc:
        print(f"rethlas-kb: write-request failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(path)
    return EXIT_OK


# ---------------------------------------------------------------------------
# write-staged-node
# ---------------------------------------------------------------------------
def _add_write_staged_node(sub) -> None:
    p = sub.add_parser(
        "write-staged-node",
        help="Stage a new node under docs/knowledge/staged/.",
        description=(
            "Reads a complete markdown file (YAML frontmatter + body) from "
            "the path passed to --from-file (or stdin if '-'), validates "
            "the frontmatter via mdblueprint, and writes it to the staged "
            "directory under the topic implied by primary_topic."
        ),
    )
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument(
        "--from-file", required=True, metavar="PATH",
        help="Markdown file (or '-' for stdin).",
    )
    p.add_argument(
        "--filename", default=None,
        help="Override the on-disk filename (default: derived from node id).",
    )
    p.set_defaults(handler=_cmd_write_staged_node)


def _cmd_write_staged_node(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    text = read_file_or_stdin(getattr(ns, "from_file"))
    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        print(
            "rethlas-kb: input is missing YAML frontmatter "
            "(expected '---\\n...\\n---\\n' at the start)",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        path = adapter.write_staged_node(
            frontmatter=frontmatter, body=body, filename=ns.filename,
        )
    except ValueError as exc:
        print(f"rethlas-kb: validation failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(path)
    return EXIT_OK


# ---------------------------------------------------------------------------
# update-staged-node-body
# ---------------------------------------------------------------------------
def _add_update_staged_node_body(sub) -> None:
    p = sub.add_parser(
        "update-staged-node-body",
        help="Replace an existing staged node's body, keep frontmatter intact.",
        description=(
            "Used by gap-filler workflows to swap in a completed proof "
            "without disturbing the node's id / kind / status / tags. "
            "Body is read from --from-file (file path or '-' for stdin). "
            "The new body must re-pass mdblueprint validation."
        ),
    )
    p.add_argument("node_id")
    p.add_argument("--blueprint", "--project", dest="blueprint", default=".", help="Blueprint root (must contain docs/knowledge/); --project accepted as legacy alias.")
    p.add_argument(
        "--from-file", required=True, metavar="PATH",
        help="New body markdown file (or '-' for stdin).",
    )
    p.set_defaults(handler=_cmd_update_staged_node_body)


def _cmd_update_staged_node_body(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE
    new_body = read_file_or_stdin(getattr(ns, "from_file"))
    try:
        path = adapter.update_staged_node_body(ns.node_id, new_body)
    except KeyError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except ValueError as exc:
        print(f"rethlas-kb: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    print(path)
    return EXIT_OK


# ---------------------------------------------------------------------------
# promote-request (v1.4 — materialise new-lemma requests as staged nodes)
# ---------------------------------------------------------------------------
def _add_promote_request(sub) -> None:
    p = sub.add_parser(
        "promote-request",
        help=(
            "Materialise a new-lemma request as a staged node (no LLM)."
        ),
        description=(
            "Reads a new-lemma request file's frontmatter "
            "(proposed_id / statement / rationale) and creates the "
            "corresponding staged node via KbAdapter.write_staged_node. "
            "Moves the request to docs/knowledge/requests/processed/ "
            "(unless --keep is passed) so subsequent sweeps don't "
            "re-promote it. Use --all-pending to batch over every "
            "new-lemma request in the requests/ dir."
        ),
    )
    p.add_argument(
        "request_path", nargs="?", default=None,
        help="Path to a single request file (omit when using --all-pending).",
    )
    p.add_argument(
        "--blueprint", "--project", dest="blueprint", default=".",
        help="Blueprint root (--project accepted as legacy alias).",
    )
    p.add_argument(
        "--all-pending", action="store_true",
        help=(
            "Promote every new-lemma request under requests/ (skip "
            "processed/). Useful as the last step in /fix-loop."
        ),
    )
    p.add_argument(
        "--keep", action="store_true",
        help="Don't move the request file to processed/ after promoting.",
    )
    p.set_defaults(handler=_cmd_promote_request)


def _cmd_promote_request(ns: argparse.Namespace) -> int:
    adapter, err = adapter_for(ns.blueprint)
    if err:
        print(f"rethlas-kb: {err}", file=sys.stderr)
        return EXIT_USAGE

    paths: list[Path] = []
    if ns.all_pending:
        if ns.request_path:
            print(
                "rethlas-kb: pass either request_path OR --all-pending, not both",
                file=sys.stderr,
            )
            return EXIT_USAGE
        paths = adapter.list_pending_requests(kind="new-lemma")
        if not paths:
            print("(no pending new-lemma requests)", file=sys.stderr)
            return EXIT_OK
    else:
        if not ns.request_path:
            print(
                "rethlas-kb: pass either a request_path OR --all-pending",
                file=sys.stderr,
            )
            return EXIT_USAGE
        paths = [Path(ns.request_path).expanduser()]

    promoted: list[str] = []
    failures: list[str] = []
    for src in paths:
        try:
            staged_path = adapter.promote_request_to_staged(
                src, mark_processed=not ns.keep,
            )
            promoted.append(str(staged_path))
            print(f"promoted: {src.name} → {staged_path}", file=sys.stderr)
        except ValueError as exc:
            failures.append(f"{src.name}: {exc}")
            print(f"rethlas-kb: failed to promote {src}: {exc}",
                  file=sys.stderr)

    # stdout: machine-readable list of staged paths
    for p in promoted:
        print(p)

    if failures and not promoted:
        return EXIT_RUNTIME
    return EXIT_OK


# ---------------------------------------------------------------------------
# validate-frontmatter
# ---------------------------------------------------------------------------
def _add_validate_frontmatter(sub) -> None:
    p = sub.add_parser(
        "validate-frontmatter",
        help="Run mdblueprint's validator against a candidate node.",
        description=(
            "Reads a markdown file from --from-file, runs "
            "mdblueprint.validate_node, prints diagnostics, exits nonzero "
            "on any error-level diagnostic."
        ),
    )
    p.add_argument("--from-file", required=True, metavar="PATH")
    p.add_argument(
        "--staged", action="store_true",
        help="Validate as if the file lives under staged/ (default: nodes/).",
    )
    p.set_defaults(handler=_cmd_validate_frontmatter)


def _cmd_validate_frontmatter(ns: argparse.Namespace) -> int:
    text = read_file_or_stdin(getattr(ns, "from_file"))
    try:
        from tools.knowledge.parser import parse_node
        from tools.knowledge.validator import validate_node
        node = parse_node(text, file_path=None)
        diags = validate_node(node, is_staged_dir=ns.staged)
    except ValueError as exc:
        print(f"rethlas-kb: parse error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    if not diags:
        print("ok")
        return EXIT_OK
    for d in diags:
        print(str(d))
    errors = [d for d in diags if d.level == "error"]
    return EXIT_RUNTIME if errors else EXIT_OK


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return ``(frontmatter_dict, body_str)`` or ``(None, "")`` on failure."""
    import re
    match = re.match(r"\A---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if match is None:
        return None, ""
    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, ""
    if not isinstance(fm, dict):
        return None, ""
    return fm, match.group(2)
