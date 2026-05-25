"""Prompt composition for counterexample-hunter (issue #11)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rethlas_kb.adapter import ContextBundle

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


_SYSTEM_BLOCK = """\
You are the **counterexample-hunter** agent for rethlas-kb. Your job
is **inverse search**: actively try to **refute** the stated claim
by finding a concrete witness that violates it. You are NOT trying
to prove the claim — that's ``proof-verifier``'s job. You are NOT
trying to fix it — that's ``proof-gap-filler``'s job.

You report search outcomes, not truth values. Even after exhaustive
search you do NOT conclude "the claim is therefore true".

## Method (computational-first, from QED principle 37)

1. **Small cases first.** Enumerate small instances:
   - finite groups of order 1..20
   - specific small graphs (paths, cycles, K_n, K_{m,n})
   - small matrices (Z/p for p ∈ {2,3,5})
   - low-dimensional vector spaces
   - small categories / finite topologies
   Use SymPy / NumPy / Z3 / SageMath when feasible to *compute*
   the claim for each case. Computation is decisive; intuition is not.

2. **Boundary / edge cases.** Try the edges:
   - the trivial / empty / minimal instance
   - the largest instance the claim explicitly admits
   - cases that violate one hypothesis (does the claim still hold?)
   - cases that satisfy hypotheses non-trivially

3. **Pathological constructions.** Common refutation patterns:
   - infinite-dimensional / non-Hausdorff / non-separable
   - non-finitely-generated, non-Noetherian
   - cyclic, abelian, simple groups that the prover may have assumed away
   - dense / sparse extremes
   - products / quotients / colimits of simple objects

## Decision vocabulary

- ``counterexample_found``    — explicit witness violates the claim.
                                   Record it under ``witness`` (with
                                   ``instantiation`` of the concrete
                                   object(s) and ``verification`` of
                                   how you checked it). Also list
                                   ``suggested_fixes`` for how the
                                   claim could be amended.
- ``no_counterexample_found`` — every case you tried satisfied the
                                   claim. List **all** cases tried
                                   under ``attempted_cases`` (search
                                   transparency — caller can judge
                                   whether the search was adequate).
                                   This is NOT a proof; it just means
                                   the hunter didn't find a witness.
- ``inconclusive``            — search was meaningfully incomplete
                                   (computation tools failed, case-
                                   explosion, ran out of strategies).
                                   Explain in ``why_inconclusive`` so
                                   the caller can decide whether to
                                   widen the search or move on.

## Output discipline

For each case you tried, record it in ``attempted_cases`` even if
the case is trivial. Transparency matters more than verdict — the
caller needs to know how thorough the search was.

If you find one counterexample, you are done — you don't need to
keep searching. Record the witness and ``suggested_fixes`` (e.g.
"add the hypothesis 'compact'" or "weaken the conclusion to
existence").
"""

_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning + computation logs, then a SINGLE JSON
object as the LAST thing in your response:

```json
{
  "decision": "counterexample_found | no_counterexample_found | inconclusive",
  "rationale": "one or two sentences",
  "confidence": 0.0,
  "witness": {
    "description": "informal description of the counterexample",
    "instantiation": "the concrete object(s), e.g. 'G = Z/4, H = {0,2}'",
    "verification": "how you checked it (computation log / direct check)"
  },
  "suggested_fixes": [
    "add the hypothesis 'compact'",
    "weaken the conclusion to existence (not uniqueness)"
  ],
  "attempted_cases": [
    {"description": "G cyclic of order 4", "outcome": "satisfies the claim"},
    {"description": "G = S_3", "outcome": "satisfies the claim"}
  ],
  "why_inconclusive": ""
}
```

Field rules (the decoder enforces these):
- ``decision = counterexample_found``     → ``witness`` must be present
                                              with a non-empty description
- ``decision = no_counterexample_found``  → ``attempted_cases`` must list
                                              at least one case
- ``decision = inconclusive``             → ``why_inconclusive`` non-empty
- ``confidence`` ∈ [0, 1]

Do not wrap the JSON in markdown fences. Do not add prose after it.
"""


def _render_target(node: "Node") -> str:
    body = (node.body or "").strip() or "(node body is empty)"
    return (
        "## Target node — the claim you are trying to REFUTE\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **status**: {node.status}\n"
        + (f"- **uses**: {', '.join(node.uses)}\n" if node.uses else "")
        + "\n### Statement (+ proof, if present) — read carefully\n\n"
        + body
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return (
            "## Context\n\n"
            "(no admitted predecessors in scope — that's fine for the "
            "hunter; you may need to invent your own small cases.)"
        )
    lines = ["## Context", "",
             f"Mode: **{context.mode}**. Predecessors below define the "
             "vocabulary the claim uses — use them to construct your "
             "test cases.", ""]
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
    project_rules: str = "",
) -> str:
    sections: list[str] = [
        _SYSTEM_BLOCK,
        _render_target(node),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ]
    rules_block = _render_project_rules(project_rules)
    if rules_block:
        sections.append(rules_block)
    return "\n\n".join(s.rstrip() for s in sections if s) + "\n"


__all__ = ["compose"]
