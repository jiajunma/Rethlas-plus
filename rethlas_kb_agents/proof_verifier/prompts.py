"""Three-stage prompt composers for proof-verifier (issue #9).

Stage prompts are intentionally scope-restricted in QED's tradition:

- ``compose_judge``      — classify difficulty, do Easy one-shot only
- ``compose_structural`` — high-level architecture ONLY; do not verify steps
- ``compose_detailed``   — step-by-step; trust the structural report

The three composers share ``_render_target`` and ``_render_context``
from the statement-verifier prompt module — but each adds a
stage-specific system block and output contract.

Project-rules sidecar (issue #22): every composer accepts
``project_rules: str = ""`` and appends it as the final section,
just like statement-verifier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rethlas_kb.adapter import ContextBundle

if TYPE_CHECKING:
    from tools.knowledge.models import Node  # pragma: no cover


# ---------------------------------------------------------------------------
# Shared rendering helpers
# ---------------------------------------------------------------------------
_PROJECT_RULES_INTRO = (
    "## Additional project rules (hard requirements)\n\n"
    "The blueprint maintainer has supplied the following project-"
    "specific rules. Treat every rule below as a hard requirement: "
    "if the proof violates any rule, the verdict must reflect the "
    "violation (do not silently override).\n\n"
)


def _render_target(node: "Node") -> str:
    body = (node.body or "").strip() or "(node body is empty)"
    return (
        "## Target node\n"
        f"- **id**: `{node.id}`\n"
        f"- **title**: {node.title}\n"
        f"- **kind**: {node.kind}\n"
        f"- **status**: {node.status}\n"
        + (f"- **uses**: {', '.join(node.uses)}\n" if node.uses else "")
        + "\n### Statement + proof (node body)\n\n"
        + body
    )


def _render_context(context: ContextBundle) -> str:
    if not context.nodes:
        return (
            "## Context\n\n"
            "(no admitted predecessors in scope — if the proof relies on a "
            "lemma you cannot find, the verdict should flag it as a gap)"
        )
    lines = [
        "## Context",
        "",
        f"Mode: **{context.mode}**. Treat any node marked "
        "*non-admitted evidence* as provisional — a proof must not depend "
        "on a non-admitted fact without flagging that dependency.",
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
    return _PROJECT_RULES_INTRO + body


def _assemble(sections: list[str]) -> str:
    return "\n\n".join(s.rstrip() for s in sections if s) + "\n"


# ===========================================================================
# Stage 1 — Judge
# ===========================================================================
_JUDGE_SYSTEM = """\
You are the **proof-verifier (judge stage)** for rethlas-kb. Your only
job in this call is to **classify the difficulty** of verifying this
proof. You are NOT verifying the statement of the theorem
(that's ``statement-verifier``).

## Classification

- ``easy`` — the proof is short, the steps are routine, and there
  is a clearly correct one-shot answer (no need for a deeper pass).
  In Easy you MUST also emit a full verdict in the same response
  (the structural and detailed stages will not run).
- ``hard`` — the proof is non-trivial enough that a careful
  step-by-step check is warranted. **When in doubt, choose hard**
  (it is better to over-verify than to miss a subtle error).

## When Easy

Pick exactly one verdict:

- ``accepted`` — the proof is correct and complete as written
- ``gap``      — the proof leaves at least one specific step
                  unjustified; list each gap in ``gaps``
- ``critical`` — the proof contains a definite error or invalid
                  step; list each error in ``critical_errors``

Citation discipline: justify your verdict by quoting the specific
sentence or formula in the proof body that supports your call.
"""

_JUDGE_OUTPUT_CONTRACT = """\
## Output contract

Reply with brief reasoning, then a SINGLE JSON object as the LAST
thing in your response:

```json
{
  "difficulty": "easy | hard",
  "rationale": "one or two sentences",
  "verdict": "accepted | gap | critical",
  "gaps": [],
  "critical_errors": [],
  "confidence": 0.0
}
```

Field rules:

- ``difficulty=hard`` — ``verdict``, ``gaps``, ``critical_errors`` are
  ignored if present; you may omit them
- ``difficulty=easy`` AND ``verdict=gap``      → ``gaps`` must be non-empty
- ``difficulty=easy`` AND ``verdict=critical`` → ``critical_errors`` must be non-empty
- ``confidence`` ∈ [0, 1]; under 0.7 means you weren't sure, in
  which case **choose hard** instead of guessing Easy

Do not wrap the JSON in markdown fences. No prose after it.
"""


def compose_judge(
    node: "Node",
    context: ContextBundle,
    *,
    project_rules: str = "",
) -> str:
    sections = [
        _JUDGE_SYSTEM,
        _render_target(node),
        _render_context(context),
        _JUDGE_OUTPUT_CONTRACT,
        _render_project_rules(project_rules),
    ]
    return _assemble(sections)


# ===========================================================================
# Stage 2 — Structural
# ===========================================================================
_STRUCTURAL_SYSTEM = """\
You are the **proof-verifier (structural stage)** for rethlas-kb. Your
scope is strictly limited to four architectural checks. **Do NOT**
verify whether individual logical steps are mathematically correct —
that is the responsibility of the detailed stage that runs after you.

## Required checks

1. **statement_quality** — Does the proof clearly identify what it
   is proving? Is the claim being proved the same as the node's
   stated theorem (quantifiers, hypotheses, conclusion all aligned
   word-for-word)? **Compare verbatim**; flag any drift.

2. **alignment** — Does the proof actually address the claim
   (rather than a special case, the converse, or an unrelated
   statement)?

3. **completeness** — Does the proof cover every case named in the
   theorem (each hypothesis, every "and"-clause, every quantifier
   range)? Are there gaps in coverage (not yet in *justification* —
   that's detailed's job — but in coverage)?

4. **architecture** — Is the proof's high-level structure sound?
   Does it announce its strategy (induction / contradiction /
   contrapositive / construction / …) and follow through? Are
   intermediate claims clearly stated even if their justification
   is brief?

## Conservative stance

If you cannot verify a check with high confidence, mark it as
**fail** and explain in the notes. Research math has unknown ground
truth — flagging is cheaper than missing a structural problem that
would have been caught later anyway.

If you find a sub-check that fails, the overall verdict must be
``fail``.
"""

_STRUCTURAL_OUTPUT_CONTRACT = """\
## Output contract

Reply with your reasoning per check, then a SINGLE JSON object as
the LAST thing in your response:

```json
{
  "verdict": "pass | fail",
  "rationale": "one or two sentences on the overall judgement",
  "checks": [
    {"name": "statement_quality", "verdict": "pass | fail", "notes": "..."},
    {"name": "alignment",         "verdict": "pass | fail", "notes": "..."},
    {"name": "completeness",      "verdict": "pass | fail", "notes": "..."},
    {"name": "architecture",      "verdict": "pass | fail", "notes": "..."}
  ],
  "confidence": 0.0
}
```

Field rules:

- All four ``name`` values are required; do not omit a check
- Each ``name`` must appear at most once
- ``verdict=fail`` requires at least one ``checks[i].verdict = "fail"``
- ``confidence`` ∈ [0, 1]

Do not wrap the JSON in markdown fences. No prose after it.
"""


def compose_structural(
    node: "Node",
    context: ContextBundle,
    *,
    project_rules: str = "",
) -> str:
    sections = [
        _STRUCTURAL_SYSTEM,
        _render_target(node),
        _render_context(context),
        _STRUCTURAL_OUTPUT_CONTRACT,
        _render_project_rules(project_rules),
    ]
    return _assemble(sections)


# ===========================================================================
# Stage 3 — Detailed
# ===========================================================================
_DETAILED_SYSTEM = """\
You are the **proof-verifier (detailed stage)** for rethlas-kb. The
structural stage has already passed (otherwise you would not have
been invoked). **You do NOT re-check structural claims** — assume
the proof's overall architecture is sound and focus on the detailed
correctness of individual steps.

## Method

1. Extract every distinct logical step from the proof body and
   assign each an id (``"1"``, ``"2"``, ``"2.a"``, …).
2. For each step, record the claim being made and check whether the
   justification in the proof actually establishes it (using the
   admitted predecessors in context).
3. Tag each step ``pass`` / ``fail`` / ``uncertain``.
4. Record **rigor issues** separately. Two severities:
   - ``fatal`` — the unjustified handwave is wrong or non-obvious
     enough that it could be hiding an error. The step it belongs
     to should be ``fail``.
   - ``minor`` — the claim is correct but the proof could use more
     detail. Note it but don't fail the step.

## Anti-handwave catalog

Flag every instance of these phrases as a rigor issue:

- "clearly" / "obviously" / "it is easy to see" / "by a standard argument"
- "similarly" without spelling out the similar derivation
- "we can assume WLOG" without justifying that the loss of generality
  is genuine
- citations to lemmas that are NOT in the context pack (gaps)
- numerical claims without computation (consider noting that the
  proof would benefit from a SymPy / NumPy check)

## Conservative stance

Under uncertainty, use ``uncertain`` (not ``pass``). If the
justification could be correct but you cannot verify it from the
context, mark the step ``uncertain`` and explain.

## Verdict aggregation rules

- All steps ``pass`` AND no fatal rigor issues → ``accepted``
- At least one ``uncertain`` step (no failures) → ``uncertain``
- At least one ``fail`` step or fatal rigor issue, but the overall
  argument is recoverable → ``gap``
- At least one ``fail`` step or fatal issue where the proof
  fundamentally cannot work as written → ``critical``
"""

_DETAILED_OUTPUT_CONTRACT = """\
## Output contract

Reply with your per-step reasoning, then a SINGLE JSON object as the
LAST thing in your response:

```json
{
  "verdict": "accepted | gap | critical | uncertain",
  "rationale": "one or two sentences on the overall judgement",
  "step_verdicts": [
    {
      "step_id": "1",
      "claim": "verbatim or close paraphrase of the step's assertion",
      "verdict": "pass | fail | uncertain",
      "notes": "what justifies this verdict; cite predecessor node ids if relied on"
    }
    // … one entry per logical step
  ],
  "rigor_issues": [
    {
      "severity": "fatal | minor",
      "claim": "the phrase or claim that is under-justified",
      "notes": "what's missing"
    }
  ],
  "confidence": 0.0
}
```

Field rules:

- ``step_verdicts`` must be non-empty (extract at least one step)
- ``verdict ∈ {gap, critical}`` requires at least one failing step or
  fatal rigor issue
- ``confidence`` ∈ [0, 1]; under 0.7 means a careful reviewer might
  disagree — consider marking borderline steps ``uncertain``

Do not wrap the JSON in markdown fences. No prose after it.
"""


def compose_detailed(
    node: "Node",
    context: ContextBundle,
    *,
    project_rules: str = "",
    structural_report: str = "",
) -> str:
    """Compose the detailed prompt. ``structural_report`` is the prior
    stage's full output, which the LLM is told to trust.
    """
    sections: list[str] = [_DETAILED_SYSTEM]
    if structural_report.strip():
        sections.append(
            "## Structural stage report (inherited — do NOT re-check)\n\n"
            + structural_report.strip()
        )
    sections.extend([
        _render_target(node),
        _render_context(context),
        _DETAILED_OUTPUT_CONTRACT,
        _render_project_rules(project_rules),
    ])
    return _assemble(sections)


__all__ = [
    "compose_detailed",
    "compose_judge",
    "compose_structural",
]
