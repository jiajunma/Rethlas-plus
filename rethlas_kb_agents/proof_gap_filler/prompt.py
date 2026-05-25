"""Prompt composition for proof-gap-filler (issue #10).

First generator-discipline prompt in rethlas-kb. The voice is
fundamentally different from verifier prompts:

- **Generators try to make something work.** Verifiers refuse if
  they can't justify acceptance. Both share an anti-handwave
  discipline, but the generator is not biased toward flagging.

- **Repair-aware**: takes the verification report from the prior
  proof-verifier run (if any) and uses it to focus the next attempt.

- **Phase II reroute** (from Rethlas-original ``generator/prompt.py``):
  when the caller signals ``repair_count >= 2``, the previous proof
  is **omitted** from context to prevent anchoring. The agent is
  told to produce a materially different proof strategy.

- **Counterexample-first** (from QED ``super_math_skill.md``):
  always try to refute the claim first. Failed refutation reveals
  the structural reason it must be true.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rethlas_kb.adapter import ContextBundle

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


# The "freshness" threshold above which we drop the previous attempt
# from context (Phase II reroute).
PHASE_II_REPAIR_THRESHOLD = 2


_SYSTEM_BLOCK_FRESH = """\
You are the **proof-gap-filler** agent for rethlas-kb. Your job is to
take a node with an incomplete or missing proof, plus the admitted
KB context, and produce a completed proof body.

This is a **fresh attempt** (no prior verification feedback).

## Method (in this order)

1. **Try to refute first.** Look for a counterexample to the
   stated claim — small cases, special instances, boundary
   conditions. If you find one, your decision is ``cannot_fill``
   and you should record the counterexample under
   ``gap_remaining`` so a human can fix the statement.

2. **Identify the strategy.** Name one: induction, contradiction,
   contrapositive, direct construction, reduction to a known
   admitted lemma, etc. Justify briefly why this strategy fits.

3. **Write the proof.** Each step must be either trivial from a
   universally-understood definition OR justified by a cited
   admitted predecessor (use ``algebra.group`` etc., not vague
   appeals to "general algebraic facts").

4. **If a needed sub-lemma is missing**, propose it as a
   ``new_sublemmas`` entry — give it a proposed id, the precise
   statement, and a one-line rationale. Continue your proof
   assuming the sub-lemma holds.

## Anti-handwave (hard rule)

The phrases "clearly", "obviously", "it is easy to see", "by a
standard argument", "WLOG" (without explicit justification of the
loss-of-generality argument), "similarly" (without spelling out the
similar derivation) are **banned**. When you feel the urge to write
one of these, that is the signal that you are about to dodge the
hard part. Either:

- Spell out the missing step explicitly, OR
- Mark the proof as ``partial`` and record the unjustified step in
  ``gap_remaining``.

It is better to say "I couldn't justify step 3" than to wave at it.
"""

_SYSTEM_BLOCK_REPAIR = """\
You are the **proof-gap-filler** agent for rethlas-kb. Your job is to
take a node whose proof was rejected by the proof-verifier, plus the
verifier's report, plus the admitted KB context, and produce an
updated proof body that addresses the verifier's specific issues.

This is a **repair attempt** (you have prior verification feedback).

## Method

1. **Read the verifier report carefully** — it identifies the
   specific gaps / errors. Your repair must address each one.

2. **Decide: local patch or rewrite?**
   - If the verifier flagged 1-2 isolated step issues, a local
     patch may suffice. Replace only the failing steps.
   - If the verifier flagged a structural problem (alignment,
     completeness, architecture), local patching usually fails —
     consider a rewrite of the affected section.

3. **Address every flagged item.** Don't silently fix some and
   ignore others.

4. **Anti-handwave still applies.** Don't replace one
   handwave-ridden step with another. If the verifier said "step 3
   uses WLOG unjustified", your fix must either provide the WLOG
   justification or eliminate the WLOG.

The verification report and previous proof are below for reference.
"""

_SYSTEM_BLOCK_PHASE_II = """\
You are the **proof-gap-filler** agent for rethlas-kb, in
**Phase II reroute mode**. This is the third or later repair
attempt — the previous strategy is stuck, and patching it further
will not converge.

The previous proof is **deliberately omitted from context** to
prevent anchoring. The verifier report and the (much earlier)
proof are both behind you.

## Method

Produce a **materially different proof strategy** for the target
claim. Concretely:

- If previous attempts used induction, try a non-inductive
  approach (direct construction, contradiction, contrapositive).
- If previous attempts assumed a specific case structure, try a
  unified treatment, or vice versa.
- If previous attempts relied on a specific admitted lemma X, try
  bypassing X (use a different admitted predecessor, or propose a
  new sub-lemma).

The previous attempts failed for a reason — repeating their
structure won't help. **Do not refer to or build on the previous
proof.** Treat this as a fresh proof of the same claim.

Anti-handwave still applies. Counterexample-first still applies.
"""


_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning first, then a SINGLE JSON object as the
LAST thing in your response. The JSON object must have these keys:

```json
{
  "decision": "filled | partial | cannot_fill",
  "rationale": "one or two sentences explaining the outcome",
  "confidence": 0.0,
  "filled_proof": "the new proof body in markdown (filled / partial only)",
  "gap_remaining": "what's still missing (partial / cannot_fill only)",
  "suggested_approaches": [
    "alternative strategies you considered or recommend"
  ],
  "new_sublemmas": [
    {
      "id": "proposed.node.id",
      "statement": "precise statement of the sub-lemma",
      "rationale": "why this is needed for the parent proof"
    }
  ]
}
```

Field rules (the decoder enforces these):
- ``decision = filled``       → ``filled_proof`` must be non-empty
- ``decision = partial``      → BOTH ``filled_proof`` AND ``gap_remaining`` non-empty
- ``decision = cannot_fill``  → ``gap_remaining`` non-empty
- ``confidence`` ∈ [0, 1]

Do not wrap the JSON in markdown fences. Do not add prose after it.
"""


def compose(
    node: "Node",
    context: ContextBundle,
    *,
    project_rules: str = "",
    prior_verification_report: str = "",
    repair_count: int = 0,
) -> str:
    """Build the gap-filler prompt.

    ``prior_verification_report`` is the markdown body of the most
    recent proof-verifier review for this node (typically the disk
    body from ``KbAdapter.write_review(... agent="proof-verifier",
    ...)``). Passing empty string means "fresh attempt".

    ``repair_count`` enables Phase II reroute when it reaches
    :const:`PHASE_II_REPAIR_THRESHOLD`.
    """
    system_block = _select_system_block(
        has_prior_report=bool(prior_verification_report.strip()),
        repair_count=repair_count,
    )
    sections: list[str] = [system_block]

    if (
        prior_verification_report.strip()
        and repair_count < PHASE_II_REPAIR_THRESHOLD
    ):
        sections.append(
            "## Previous proof-verifier report\n\n"
            + prior_verification_report.strip()
        )

    sections.extend([
        _render_target(
            node,
            include_body=repair_count < PHASE_II_REPAIR_THRESHOLD,
            repair_count=repair_count,
        ),
        _render_context(context),
        _OUTPUT_CONTRACT,
    ])
    rules_block = _render_project_rules(project_rules)
    if rules_block:
        sections.append(rules_block)
    return "\n\n".join(s.rstrip() for s in sections if s) + "\n"


def _select_system_block(*, has_prior_report: bool, repair_count: int) -> str:
    if repair_count >= PHASE_II_REPAIR_THRESHOLD:
        return _SYSTEM_BLOCK_PHASE_II
    if has_prior_report:
        return _SYSTEM_BLOCK_REPAIR
    return _SYSTEM_BLOCK_FRESH


# ---------------------------------------------------------------------------
# Rendering helpers (separate from statement_verifier — generator has
# different rendering needs around proof-body inclusion vs omission)
# ---------------------------------------------------------------------------
def _render_target(node: "Node", *, include_body: bool, repair_count: int) -> str:
    head = (
        "## Target node\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **status**: {node.status}\n"
        + (f"- **uses**: {', '.join(node.uses)}\n" if node.uses else "")
        + (f"- **repair attempt**: #{repair_count}\n" if repair_count > 0 else "")
    )
    if include_body:
        body = (node.body or "").strip() or "(node body is empty)"
        return head + "\n### Current statement + proof (node body)\n\n" + body
    return (
        head
        + "\n### Statement\n\n"
        + "(The previous proof body is intentionally omitted for "
        "Phase II reroute. Recover the statement from the node's "
        "frontmatter title and the context pack predecessors. If "
        "the statement is itself ambiguous without the proof body, "
        "say so and request a fresh statement-verifier pass.)"
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return (
            "## Context\n\n"
            "(no admitted predecessors in scope — a generator with no "
            "admitted KB context will struggle. Consider whether you "
            "can propose a new sub-lemma rather than handwaving.)"
        )
    lines = [
        "## Context",
        "",
        f"Mode: **{context.mode}**. Treat any node marked "
        "*non-admitted evidence* as provisional — a proof that "
        "depends on a non-admitted fact should propose admitting "
        "that fact as a separate sub-lemma.",
        "",
    ]
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


__all__ = ["PHASE_II_REPAIR_THRESHOLD", "compose"]
