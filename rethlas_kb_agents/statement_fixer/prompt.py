"""Prompt composition for statement-fixer (v1.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rethlas_kb.adapter import ContextBundle

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


_SYSTEM_BLOCK = """\
You are the **statement-fixer** agent for rethlas-kb. Your job is to
take a staged node whose statement was flagged by statement-verifier
and produce a corrected statement.

You are NOT verifying the statement (that's statement-verifier). You
are NOT proving anything (that's proof-verifier / fill-gap). Your
only job is to repair the statement text so the same statement-
verifier check would pass.

## Decisions

- ``fixed``             — produce a corrected ``fixed_body`` that
                           addresses every issue in the prior review.
                           List each issue you addressed under
                           ``addressed_issues``.
- ``cannot_fix``        — the issue is fundamental (e.g. the
                           statement is genuinely ambiguous, or the
                           generality concern requires research-level
                           judgement). Explain in ``blocker``.
- ``defers_to_human``   — fixing would require changing the
                           mathematical claim in ways an LLM shouldn't
                           decide unilaterally (e.g. weakening a
                           conclusion when that conclusion might be
                           the whole point). Explain in ``blocker``.

## Hard rules

- **Anti-handwave**: don't paper over issues with vaguer wording.
  If statement-verifier flagged ``formulation_issue: quantifier
  drift``, your fix must make the quantifier explicit, not soften
  it.
- **Don't broaden the claim**. If the original says "for all finite
  groups G", don't widen to "for all groups G" — that changes the
  math. Same for narrowing without good reason.
- **Preserve any proof body** that's already present. Your job is
  the statement; the proof is proof-verifier / fill-gap territory.
  Only edit the statement portion of the body.
- **Cite admitted predecessors** from the context pack when
  justifying terminology choices (e.g. "using ``algebra.group``'s
  convention that groups are non-empty").

## Fix priority

When multiple issues are flagged, address them in this order:
1. ``missing_definitions`` — pick a different term that IS defined,
   or note that you can't fix without a new def (then ``cannot_fix``)
2. ``formulation_issues`` — typos / quantifier drift / missing
   hypotheses (these are the most fixable)
3. ``generality_notes`` — judgement calls. Often defer to human.
4. ``context_gap_notes`` — usually means rerun with wider context;
   ``cannot_fix`` is the right answer.
"""


_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning first, then a SINGLE JSON object as the
LAST thing in your response:

```json
{
  "decision": "fixed | cannot_fix | defers_to_human",
  "rationale": "one or two sentences",
  "confidence": 0.0,
  "fixed_body": "the corrected node body, markdown (only for decision=fixed)",
  "addressed_issues": [
    "verbatim quote of each issue from the prior review that you addressed"
  ],
  "blocker": "what's stopping the fix (for cannot_fix / defers_to_human)"
}
```

Field rules (decoder enforces):
- ``decision = fixed`` → ``fixed_body`` non-empty
- ``decision = cannot_fix`` → ``blocker`` non-empty
- ``decision = defers_to_human`` → ``blocker`` non-empty
- ``confidence`` ∈ [0, 1]

Do not wrap the JSON in markdown fences. No prose after it.
"""


def _render_prior_review(prior_review: str) -> str:
    text = (prior_review or "").strip()
    if not text:
        return (
            "## Prior statement-verifier review\n\n"
            "(No prior review supplied — you have no specific issues "
            "to fix. Choose ``defers_to_human`` and explain that the "
            "caller must provide a verifier review to act on.)"
        )
    return "## Prior statement-verifier review\n\n" + text


def _render_target(node: "Node") -> str:
    body = (node.body or "").strip() or "(node body is empty)"
    return (
        "## Target node — the statement you must fix\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **status**: {node.status}\n"
        + (f"- **uses**: {', '.join(node.uses)}\n" if node.uses else "")
        + "\n### Current body (statement + possibly proof)\n\n"
        + body
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return "## Context\n\n(no admitted predecessors in scope)"
    lines = ["## Context", "",
             f"Mode: **{context.mode}**. Admitted predecessors set "
             "the vocabulary you can use in the fixed statement.", ""]
    for entry in context.nodes:
        nid = entry.get("id", "<no-id>")
        if nid == context.target_id:
            continue
        evidence = entry.get("evidence", "admitted")
        title = entry.get("title", "")
        kind = entry.get("kind", "")
        status = entry.get("status", "")
        body = (entry.get("body") or "").strip()
        marker = "" if evidence == "admitted" else " *(non-admitted evidence)*"
        lines.append(f"### `{nid}` — {title}{marker}")
        lines.append(f"_{kind} · status={status}_")
        if body:
            lines.append("")
            lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_project_rules(project_rules: str) -> str:
    body = (project_rules or "").strip()
    if not body:
        return ""
    return (
        "## Additional project rules (hard requirements)\n\n"
        + body
    )


def compose(
    node: "Node",
    context: ContextBundle,
    *,
    prior_review: str = "",
    project_rules: str = "",
) -> str:
    sections: list[str] = [
        _SYSTEM_BLOCK,
        _render_prior_review(prior_review),
        _render_target(node),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ]
    rules_block = _render_project_rules(project_rules)
    if rules_block:
        sections.append(rules_block)
    return "\n\n".join(s.rstrip() for s in sections if s) + "\n"


__all__ = ["compose"]
