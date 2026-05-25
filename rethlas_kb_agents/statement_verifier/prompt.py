"""Prompt composition for statement-verifier (issue #7).

Pure function — no I/O, no LLM calls. Takes a target Node + the
:class:`ContextBundle` returned by ``KbAdapter.context_pack`` and
returns a single self-contained string the backend can hand to a
CLI.

The prompt is intentionally explicit about the output contract so
the decoder's "last balanced JSON blob" sweep has something to find.
"""

from __future__ import annotations

from rethlas_kb.adapter import ContextBundle

# Imported lazily-typed via TYPE_CHECKING to avoid a hard runtime
# dependency on mdblueprint types at prompt-build time.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


_SYSTEM_BLOCK = """\
You are the **statement-verifier** agent for the rethlas-kb knowledge base.

Your job is to judge whether the *statement* of one mathematical node
(definition, lemma, proposition, theorem, or external-theorem) is
correctly formulated. You are NOT asked to verify the proof. The
``proof-verifier`` agent owns proof correctness.

Choose exactly one decision:

- ``accepted``            — the statement reads correctly and is ready
                             to be relied on by downstream nodes.
- ``needs_definition``    — the statement uses a term that has no
                             admitted definition in the KB. Populate
                             ``missing_definitions`` with the missing
                             concept name(s).
- ``generality_concern``  — the statement is true as written but is
                             stated less generally than its hypotheses
                             warrant, or generality is unclear.
                             Explain in ``generality_notes``.
- ``formulation_issue``   — the statement has a typo, dangling
                             quantifier, ambiguity, or other surface
                             error. List specifics in
                             ``formulation_issues``.

Cite node IDs from the context pack when justifying your decision.
Do not invent claims that are not supported by the context.
"""

_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning first, then a SINGLE JSON object as the
LAST thing in your response. The JSON object must have these keys:

```json
{
  "decision": "accepted | needs_definition | generality_concern | formulation_issue",
  "rationale": "one or two sentences explaining the decision",
  "confidence": 0.0,                // float in [0, 1]
  "missing_definitions": [],        // required when decision = needs_definition
  "formulation_issues": [],         // required when decision = formulation_issue
  "generality_notes": ""            // required when decision = generality_concern
}
```

Do not wrap the JSON in markdown fences. Do not add prose after it.
"""


def compose(node: "Node", context: ContextBundle) -> str:
    """Build the full prompt string for one node.

    Layout:
      1. System block (role + decisions).
      2. The target node (frontmatter highlights + body).
      3. Context pack — admitted predecessors (and staged evidence
         flagged when present).
      4. Output contract.
    """
    sections = [
        _SYSTEM_BLOCK,
        _render_target(node),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ]
    return "\n\n".join(s.rstrip() for s in sections) + "\n"


# ---------------------------------------------------------------------------
# Rendering helpers (kept separate so tests can target them directly)
# ---------------------------------------------------------------------------
def _render_target(node: "Node") -> str:
    body = (node.body or "").strip() or "(node body is empty)"
    return (
        "## Target node\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **status**: {node.status}\n"
        + (f"- **uses**: {', '.join(node.uses)}\n" if node.uses else "")
        + "\n### Statement (body)\n\n"
        + body
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return "## Context\n\n(no admitted predecessors in scope)"

    lines = [
        "## Context",
        "",
        f"Mode: **{context.mode}**. Treat any node marked "
        "*non-admitted evidence* as provisional — not as a proven fact.",
        "",
    ]
    for entry in context.nodes:
        nid = entry.get("id", "<no-id>")
        if nid == context.target_id:
            # Skip the target itself; it's already rendered above.
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


__all__ = ["compose"]
