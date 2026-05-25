"""Prompt composition for statement-verifier (issue #7, hardened from QED).

Pure function — no I/O, no LLM calls. Takes a target Node + the
:class:`ContextBundle` returned by ``KbAdapter.context_pack`` and
returns a single self-contained string the backend can hand to a
CLI.

The prompt embodies discipline borrowed from
``~/mycodes/QED/verify/prompt_verify_*.md`` (see docstrings on the
relevant sections):

- Conservative-by-default stance: under uncertainty, the agent must
  pick the decision that flags a problem rather than ``accepted``.
- Verbatim-quote discipline: the output requires a literal copy of
  the statement being judged, so the agent cannot paraphrase itself
  into a false agreement.
- Anti-pattern catalog: explicit enumeration of common formulation
  defects (changed quantifiers, restricted domain, missing
  hypotheses, etc.) so the agent has a concrete checklist.
- ``context_insufficient`` escape hatch: a fifth decision that
  signals the context pack is too narrow for a confident verdict,
  so the caller can re-run with a wider context.
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

## Conservative stance (read this carefully)

You are reviewing **new research mathematics**, not formalized
textbook content. The ground truth is unknown. **Under uncertainty,
prefer the decision that flags a problem.** Specifically:

- If you are unsure whether a term used in the statement is defined
  by an admitted predecessor in the context pack, choose
  ``needs_definition``.
- If the statement looks correct but you cannot fully verify the
  generality claim (e.g. you can't confirm a hypothesis is actually
  used), choose ``generality_concern``.
- If you suspect but cannot pin down a formulation issue, still
  choose ``formulation_issue`` and describe what feels wrong.
- If the context pack appears to be missing predecessor nodes that
  you would need to make a confident judgement, choose
  ``context_insufficient`` rather than guessing.
- Only choose ``accepted`` when you can articulate a positive reason
  the statement is well-formed (not merely the absence of obvious
  errors).

It is much better to over-flag and require a human review than to
silently admit a malformed statement that downstream proofs rely on.

## Decisions

- ``accepted``             — the statement reads correctly AND every
                              technical term it uses has either a
                              standard mathematical meaning or an
                              admitted definition in the context.
- ``needs_definition``     — the statement uses a non-standard term
                              with no admitted definition in the KB.
                              Populate ``missing_definitions`` with
                              the missing concept name(s).
- ``generality_concern``   — the statement is true as written but
                              less general than its hypotheses warrant
                              (a hypothesis appears unused or
                              redundant), OR more general than the
                              hypotheses can support. Explain in
                              ``generality_notes`` which hypothesis
                              and why.
- ``formulation_issue``    — the statement has a concrete defect.
                              See the anti-pattern catalog below.
                              List specifics in
                              ``formulation_issues``.
- ``context_insufficient`` — you would need more predecessor nodes
                              (or the source paper, or notation
                              conventions from a sibling topic) to
                              judge confidently. Explain in
                              ``context_gap_notes`` what is missing.
                              The caller will re-run with a wider
                              context.

## Anti-pattern catalog for formulation_issue

When the statement has one of these defects, flag it as
``formulation_issue`` and quote the offending phrase:

1. **Quantifier drift** — "for all" silently swapped with "there
   exists", or a free variable that should be bound.
2. **Domain restriction** — the statement reads "for integers" but
   the natural domain (and the use sites) need reals; or vice versa.
3. **Strengthened or weakened hypotheses** — adds an unnecessary
   condition (e.g. "compact Hausdorff" where only "compact" is used),
   or drops a condition the conclusion depends on (e.g. "Noetherian"
   when finite generation is actually required).
4. **Missing uniqueness / existence** — claims "the X" when "an X"
   is what's defined; or claims existence without a uniqueness
   counterpart when one is implied.
5. **Implicit regularity hypotheses** — silently assumes
   continuity, measurability, integrability, smoothness, or
   compactness without stating it.
6. **Swapped conclusion and hypothesis** — proving the converse of
   what was stated.
7. **Dangling notation** — symbols or operators introduced without
   definition (or whose definition is in a sibling node that isn't
   in scope — in that case prefer ``context_insufficient``).
8. **Modified constants or bounds** — inequality direction flipped,
   strict vs non-strict swapped, or constant changed.

Citation discipline: every claim in your rationale must either
quote the target statement verbatim or cite a node id from the
context pack (e.g. "per ``algebra.group``, the operation is
associative"). Do not invent claims that are not supported by the
context.
"""

_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning first, then a SINGLE JSON object as the
LAST thing in your response. The JSON object must have these keys:

```json
{
  "decision": "accepted | needs_definition | generality_concern | formulation_issue | context_insufficient",
  "quoted_statement": "the verbatim text of the statement you judged (copy from the Target node body, preserving notation)",
  "rationale": "one or two sentences referencing the quoted statement and/or cited node ids",
  "confidence": 0.0,
  "missing_definitions": [],
  "formulation_issues": [],
  "generality_notes": "",
  "context_gap_notes": ""
}
```

Confidence guidance:
- 0.9–1.0 — the decision is obvious and you'd defend it under cross-examination
- 0.7–0.9 — the decision is right but a careful reviewer might quibble
- 0.5–0.7 — you had to make a judgement call; reviewer might reasonably disagree
- below 0.5 — you're guessing; reconsider whether ``context_insufficient`` fits better

Required-field rules (the decoder enforces these):
- ``decision = needs_definition``        → ``missing_definitions`` must be non-empty
- ``decision = formulation_issue``       → ``formulation_issues`` must be non-empty
- ``decision = generality_concern``      → ``generality_notes`` must be non-empty
- ``decision = context_insufficient``    → ``context_gap_notes`` must be non-empty

Do not wrap the JSON in markdown fences. Do not add prose after it.
"""


def compose(
    node: "Node",
    context: ContextBundle,
    *,
    project_rules: str = "",
) -> str:
    """Build the full prompt string for one node.

    Layout:
      1. System block (role + conservative stance + decisions +
         anti-pattern catalog).
      2. The target node (frontmatter highlights + body).
      3. Context pack — admitted predecessors (and staged evidence
         flagged when present).
      4. Output contract (with verbatim-quote requirement +
         confidence guidance + per-decision required fields).
      5. Project-specific rules sidecar (issue #22) — appended only
         when ``project_rules`` is non-empty. Treated as hard
         requirements per QED's Phase-5 pattern.
    """
    sections: list[str] = [
        _SYSTEM_BLOCK,
        _render_target(node),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ]
    rules_block = _render_project_rules(project_rules)
    if rules_block:
        sections.append(rules_block)
    return "\n\n".join(s.rstrip() for s in sections) + "\n"


def _render_project_rules(project_rules: str) -> str:
    """Render the optional project-rules sidecar as a final prompt section."""
    body = (project_rules or "").strip()
    if not body:
        return ""
    return (
        "## Additional project rules (hard requirements)\n\n"
        "The blueprint maintainer has supplied the following project-"
        "specific rules. Treat every rule below as a hard requirement: "
        "if the target statement violates any rule, choose the "
        "decision that flags the violation (do not silently override).\n\n"
        + body
    )


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
        return (
            "## Context\n\n"
            "(no admitted predecessors in scope — if the target statement "
            "uses notation or terms you cannot resolve, prefer "
            "``context_insufficient``)"
        )

    lines = [
        "## Context",
        "",
        f"Mode: **{context.mode}**. Treat any node marked "
        "*non-admitted evidence* as provisional — not as a proven fact. "
        "If you need a predecessor that's not listed here, choose "
        "``context_insufficient`` rather than guessing.",
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
