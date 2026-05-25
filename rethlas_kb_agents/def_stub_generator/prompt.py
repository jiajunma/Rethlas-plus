"""Prompt composition for def-stub-generator (v1.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rethlas_kb.adapter import ContextBundle

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


_SYSTEM_BLOCK = """\
You are the **def-stub-generator** agent for rethlas-kb. Your job is
to create a staged definition stub for a missing term. Another
agent (typically statement-verifier) flagged this term as
``needs_definition``; your output materialises the missing node so
the dependency graph is complete.

Your goal is **"good enough to unblock"**, not "polished
definition". A placeholder with correct frontmatter is far better
than a missing node.

## Decisions

- ``drafted``           — you wrote a real first-pass definition body
                           that captures the standard meaning. The
                           caller will save it as a staged node; a
                           human can refine later.
- ``placeholder_only``  — you set up correct frontmatter (id, title,
                           primary_topic, topics) but the body is
                           just a clearly-marked TODO. The
                           dependency graph gets the node; a human
                           writes the actual definition.
- ``cannot_stub``       — the proposed name is meaningless, ambiguous,
                           or describes something that genuinely
                           doesn't have a standard definition.
                           Explain in ``blocker``.

## Conservative stance

When you don't know the definition with confidence, choose
``placeholder_only``. **Don't invent a wrong definition** — that
poisons every downstream proof. Placeholder + TODO is honest;
inventive but wrong is harmful.

## Frontmatter rules

- ``proposed_id``    — typically ``<topic>.<snake_case_name>``, e.g.
                        ``algebra.normal_subgroup``. Use the calling
                        node's primary_topic when in doubt.
- ``title``          — human-readable, e.g. "Normal Subgroup"
- ``primary_topic``  — derive from id (the part before the dot)
- ``topics``         — typically [primary_topic]; add others if relevant
- ``uses``           — list any admitted predecessors the definition
                        relies on (e.g. ``algebra.group`` for a
                        normal-subgroup stub). Only list things in
                        the context pack.

## Body rules

- ``drafted``: write a one-paragraph standard definition; use LaTeX
  math, cite predecessors. Use a ``# Title`` heading at the top.
- ``placeholder_only``: write something like:
    ```
    # <title>

    **TODO:** Definition not yet supplied. This stub was auto-created
    by def-stub-generator to extend the dependency graph; a human
    should write the actual definition before any downstream proof
    relies on this node.

    Context: needed by [[node:<referring_node_id>]] for <reason>.
    ```
"""


_OUTPUT_CONTRACT = """\
## Output contract

Reply with brief reasoning, then a SINGLE JSON object as the LAST
thing in your response:

```json
{
  "decision": "drafted | placeholder_only | cannot_stub",
  "rationale": "one or two sentences on what you did",
  "confidence": 0.0,
  "proposed_id": "topic.snake_case_name",
  "title": "Human Title",
  "primary_topic": "topic",
  "topics": ["topic"],
  "uses": ["admitted.predecessor.ids"],
  "body": "the markdown body (definition or TODO placeholder)",
  "blocker": "explanation when cannot_stub"
}
```

Field rules (decoder enforces):
- ``drafted``  / ``placeholder_only`` → ``proposed_id`` + ``title`` + ``body`` non-empty
- ``cannot_stub`` → ``blocker`` non-empty
- ``confidence`` ∈ [0, 1]

Do not wrap the JSON in markdown fences. No prose after it.
"""


def _render_request(missing_id: str, referring_node_id: str, reason: str) -> str:
    return (
        "## Missing-definition request\n\n"
        f"- **proposed id**: `{missing_id}`\n"
        f"- **flagged by**: `{referring_node_id}`\n"
        + (f"- **reason**: {reason}\n" if reason else "")
    )


def _render_referring_node(node: "Node") -> str:
    body = (node.body or "").strip() or "(empty body)"
    return (
        "## Referring node — for context on how the missing term is used\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **primary_topic**: {node.primary_topic or '—'}\n"
        "\n### Body (where the missing term appears)\n\n"
        + body
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return "## Context\n\n(no admitted predecessors in scope)"
    lines = ["## Context (already-admitted nodes you can cite)", ""]
    for entry in context.nodes:
        nid = entry.get("id", "<no-id>")
        if nid == context.target_id:
            continue
        title = entry.get("title", "")
        kind = entry.get("kind", "")
        status = entry.get("status", "")
        body = (entry.get("body") or "").strip()
        lines.append(f"### `{nid}` — {title}")
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
    return "## Additional project rules (hard requirements)\n\n" + body


def compose(
    missing_id: str,
    referring_node: "Node",
    context: ContextBundle,
    *,
    reason: str = "",
    project_rules: str = "",
) -> str:
    sections: list[str] = [
        _SYSTEM_BLOCK,
        _render_request(missing_id, referring_node.id, reason),
        _render_referring_node(referring_node),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ]
    rules_block = _render_project_rules(project_rules)
    if rules_block:
        sections.append(rules_block)
    return "\n\n".join(s.rstrip() for s in sections if s) + "\n"


__all__ = ["compose"]
