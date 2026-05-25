"""Prompt composition for source-claim-verifier (issue #12)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rethlas_kb.adapter import ContextBundle

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


_SYSTEM_BLOCK = """\
You are the **source-claim-verifier** agent for rethlas-kb. The target
node is an ``external-theorem`` — a theorem cited from a published
source (paper, textbook, preprint). Your job is to verify two things:

1. **Alignment**: does the node's statement faithfully reproduce the
   source paper's statement? (no quantifier drift, no dropped
   hypotheses, no widened conclusion)
2. **Source-proof soundness** (when the source proof text is
   provided): does the source paper's proof actually establish the
   claim, or does it have a gap / error that you would also flag in
   our own proofs?

There is **no special treatment** for arXiv preprints vs peer-
reviewed publications: ``source_kind`` is informational only. The
verification IS the gate. Treat them identically.

## Verbatim-quote discipline (hard rule)

Both ``quoted_node_statement`` and ``quoted_source_statement`` are
required output fields. You must **copy the actual text** of each
verbatim (preserving notation), then compare the two in your
``rationale``. If your rationale claims a difference, the two quoted
fields must show it.

## Conservative stance

Under uncertainty about whether the alignment holds, pick
``cannot_verify`` and explain in ``missing_evidence``. Never guess.
The blueprint maintainer can re-run with a wider source passage.

## Decisions

- ``accepted``       — alignment OK AND (if source proof provided) the
                        source proof is sound to your standards
- ``mismatch``       — node statement differs from source statement;
                        list each difference under ``differences``
- ``proof_gap``      — alignment OK, but source proof has a recoverable
                        gap; list each gap under ``proof_issues``
- ``proof_critical`` — alignment OK, but source proof has a fundamental
                        error; list under ``proof_issues``
- ``cannot_verify``  — source passage too short / missing / unclear to
                        judge; explain in ``missing_evidence``

## Anti-handwave (same standards as proof-verifier)

When evaluating the source proof, flag "clearly / obviously / WLOG
without justification" as ``proof_gap`` issues. The source paper
being published doesn't grant it immunity.
"""

_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning first, then a SINGLE JSON object as the
LAST thing in your response:

```json
{
  "decision": "accepted | mismatch | proof_gap | proof_critical | cannot_verify",
  "rationale": "one or two sentences",
  "confidence": 0.0,
  "quoted_node_statement": "verbatim copy of the node's stated theorem",
  "quoted_source_statement": "verbatim copy of the source paper's stated theorem (or '' if not provided)",
  "differences": ["concrete difference 1", "concrete difference 2"],
  "proof_issues": ["proof issue 1", "proof issue 2"],
  "missing_evidence": "what additional source text would let you decide"
}
```

Field rules:
- ``decision = mismatch``       → ``differences`` non-empty
- ``decision = proof_gap``      → ``proof_issues`` non-empty
- ``decision = proof_critical`` → ``proof_issues`` non-empty
- ``decision = cannot_verify``  → ``missing_evidence`` non-empty
- ``confidence`` ∈ [0, 1]

Do not wrap the JSON in markdown fences. No prose after it.
"""


def _render_target(node: "Node") -> str:
    body = (node.body or "").strip() or "(node body is empty)"
    return (
        "## Target node — the external-theorem you are auditing\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **status**: {node.status}\n"
        + (f"- **uses**: {', '.join(node.uses)}\n" if node.uses else "")
        + "\n### Node body (your statement of the external theorem)\n\n"
        + body
    )


def _render_source_passage(source_passage: str) -> str:
    text = (source_passage or "").strip()
    if not text:
        return (
            "## Source passage\n\n"
            "(No pre-extracted source passage was provided. Without it "
            "you cannot verify alignment — your decision should be "
            "``cannot_verify`` and you should request the passage via "
            "``missing_evidence``.)"
        )
    return (
        "## Source passage (pre-extracted from the cited paper)\n\n"
        "Treat this as ground truth for what the source says. Compare\n"
        "the node body's statement against this verbatim.\n\n"
        + text
    )


def _render_source_proof(source_proof: str) -> str:
    text = (source_proof or "").strip()
    if not text:
        return (
            "## Source proof\n\n"
            "(No source proof text provided. Only verify alignment; "
            "do NOT flag proof_gap or proof_critical without proof text.)"
        )
    return (
        "## Source proof (extracted from the cited paper)\n\n"
        "Verify whether this proof establishes the claim, with the same\n"
        "anti-handwave discipline you would apply to our own proofs.\n\n"
        + text
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return "## Context\n\n(no admitted predecessors in scope)"
    lines = ["## Context", "",
             f"Mode: **{context.mode}**. These are the admitted "
             "predecessors the external theorem depends on.", ""]
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
        "The blueprint maintainer has supplied the following project-"
        "specific rules. Treat every rule below as a hard requirement.\n\n"
        + body
    )


def compose(
    node: "Node",
    context: ContextBundle,
    *,
    source_passage: str = "",
    source_proof: str = "",
    project_rules: str = "",
) -> str:
    sections: list[str] = [
        _SYSTEM_BLOCK,
        _render_target(node),
        _render_source_passage(source_passage),
        _render_source_proof(source_proof),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ]
    rules_block = _render_project_rules(project_rules)
    if rules_block:
        sections.append(rules_block)
    return "\n\n".join(s.rstrip() for s in sections if s) + "\n"


__all__ = ["compose"]
