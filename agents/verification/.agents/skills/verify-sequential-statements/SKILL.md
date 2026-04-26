---
name: verify-sequential-statements
description: Verify one target proof in textual order using only the prompt and verified node files.
---

# Verify Sequential Statements

Check the current target proof step by step. This skill is a reasoning
procedure, not a persistence workflow.

## Input Contract

Use only:

- `Statement` from the prompt
- `Proof` from the prompt
- `Kind` from the prompt
- verified statements explicitly resolved from `knowledge_base/nodes/`

Do not use MCP tools, web search, arXiv search, events, runtime logs, or
generator memory. Do not generate a replacement proof.

## Procedure

1. Extract the target kind, assumptions, and conclusion from the prompt.
2. If kind is `lemma`, `proposition`, or `theorem` and proof is empty or
   unusable, record a gap before any further checking.
   If kind is `definition` or `external_theorem`, an empty proof is
   expected (these are axioms in Phase I) — do not record a gap on
   that basis. For these kinds, only check that the statement is
   internally coherent (no contradiction, no undefined notation in the
   statement itself, domain terminology used consistently); skip
   per-step proof verification entirely. Dependency resolution for any
   `\ref{label}` in the statement is owned by
   `$check-referenced-statements` — do not redo it here.
3. Read the proof in textual order.
4. For each meaningful claim or inference, choose a stable location:
   - displayed claim/lemma name if present,
   - otherwise `proof paragraph N`.
5. Check local validity:
   - the inference follows from earlier proof text, the target assumptions,
     or explicitly referenced verified statements;
   - all hypotheses of a referenced statement are supplied;
   - definitions and notation are used consistently;
   - no circular use of the target statement occurs.
6. Classify issues. The `checked_items.status` field accepts exactly
   `accepted|gap|critical`; an issue that is itself a gap or critical
   error must also surface in the corresponding `gaps[]` /
   `critical_errors[]` list during synthesis.
   - `gap`: missing derivation, vague justification, omitted hypothesis
     check, or insufficient evidence.
   - `critical`: contradiction, false implication, circular argument,
     invalid dependency use, or a core claim that appears wrong.
7. When unsure, classify as `gap`.

## Output Contribution

Prepare `checked_items` entries like:

```json
{
  "location": "proof paragraph 3",
  "status": "gap",
  "notes": "Boundedness is asserted but not derived from the hypotheses."
}
```

For every issue classified `gap` or `critical` in step 6, also prepare
a parallel entry for the corresponding `gaps[]` or `critical_errors[]`
list using this shape (`location` should match the `checked_items`
entry):

```json
{
  "location": "proof paragraph 3",
  "issue": "Boundedness is asserted but not derived from the hypotheses."
}
```

Contribute these to `$synthesize-verification-report` for assembly into
the final report. Do not write files.
